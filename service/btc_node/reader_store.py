"""Run the existing Telegram reader against the Docker node's MySQL and CAS."""

from contextlib import contextmanager
from importlib import import_module
import json
from pathlib import Path
import sys
from threading import RLock
from time import time

from .codec import Conflict, identity, millis
from .database import Database
from .reader_schema import ARCHIVED

MODE = 'reader.storage'
NATIVE = 'MYSQL_V1'


def native(db):
    return (db.metadata(MODE) or {}).get('backend') == NATIVE


def reader_module(settings, name):
    config = json.loads(Path(settings['container_node_config']).read_bytes())
    path = str(Path(config['reader_code_dir']).resolve())
    if path not in sys.path:
        sys.path.insert(0, path)
    module = import_module(name)
    if Path(module.__file__).resolve().parent != Path(path):
        raise Conflict('Docker reader dependency differs from the configured release')
    return module


def _archive_row(db, path, table, *key):
    row = db.one('SELECT digest FROM legacy_rows WHERE database_key=%s AND table_name=%s AND row_key=%s',
                 (identity('reader', path), table, identity(*key)))
    return db.value(row['digest']) if row else None


def seed(db, *, expected_checkpoint_ms=None):
    """Promote the final committed reader checkpoint within the node writer lock."""
    if native(db):
        return db.metadata(MODE)
    checkpoint_ms = (db.metadata('import:reader') or {}).get('checked_ms') or (db.metadata('reader.checkpoint') or {}).get('at_ms')
    if expected_checkpoint_ms is not None and checkpoint_ms != expected_checkpoint_ms:
        raise Conflict('final reader checkpoint changed before cutover')
    cursor = _archive_row(db, 'messages.sqlite3', 'sync_state', 1)
    config = _archive_row(db, 'messages.sqlite3', 'reader_config', 1)
    if not checkpoint_ms or not cursor or not config:
        raise Conflict('a complete final reader checkpoint is required before cutover')
    counts = {}
    for path, tables in ARCHIVED.items():
        database_key = identity('reader', path)
        if not db.one('SELECT 1 FROM legacy_databases WHERE database_key=%s', (database_key,)):
            raise Conflict('reader checkpoint lacks ' + path)
        for table in tables:
            if db.one(f'SELECT 1 FROM {table} LIMIT 1'):
                raise Conflict('native reader table already has data before cutover: ' + table)
            counts[table] = _seed_table(db, database_key, table)
    expected = cursor['last_update_id']
    if db.one('SELECT last_update_id FROM sync_state WHERE singleton_id=1')['last_update_id'] != expected:
        raise Conflict('native reader cursor differs from its checkpoint')
    return db.metadata(MODE, {'backend': NATIVE, 'checkpoint_ms': checkpoint_ms,
                              'started_ms': millis(), 'initial_cursor': expected,
                              'migrated_rows': counts}, write=True)


def _seed_table(db, database_key, table):
    after, count = '', 0
    while True:
        page = db.rows('SELECT row_key,digest FROM legacy_rows WHERE database_key=%s AND table_name=%s '
                       'AND row_key>%s ORDER BY row_key LIMIT 256', (database_key, table, after))
        if not page:
            return count
        for row in page:
            value = db.value(row['digest'])
            if table == 'collector_blobs':
                if value['raw'] != {'$binary_content': value['digest']}:
                    raise Conflict('reader checkpoint lost its original attachment binding')
                value = {'digest': value['digest']}
            elif table == 'collector_validation_history':
                original = db.one('SELECT implicit_rowid FROM legacy_row_identity WHERE database_key=%s '
                                  'AND table_name=%s AND row_key=%s', (database_key, table, row['row_key']))
                if original and original['implicit_rowid'] is not None:
                    value['validation_id'] = original['implicit_rowid']
            columns = sorted(value)
            if any(not c.isascii() or not c.replace('_', '').isalnum() for c in columns):
                raise Conflict('invalid reader archive column')
            fields = ','.join('`' + c + '`' for c in columns)
            parameters = ','.join(['%s'] * len(columns))
            db.execute(f'INSERT INTO {table}({fields}) VALUES({parameters})',
                       tuple(value[c] for c in columns))
            count += 1
        after = page[-1]['row_key']


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return next(iter(self.rows), None)

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class ReaderConnection:
    """Adapt the pinned reader's storage statements to its native node tables."""

    _duplicates = {
        'collector_revisions': 'revision', 'collector_attachments': 'revision',
        'collector_update_conflicts': 'digest', 'collector_download_failures': 'attempt',
        'message_revisions': 'content_fingerprint',
    }

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        # MessageStore.apply_sync_batch joins the surrounding node transaction.
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=()):
        sql = ' '.join(statement.split())
        if sql == 'SELECT raw FROM collector_blobs WHERE digest=?':
            row = self.db.one('SELECT digest FROM collector_blobs WHERE digest=%s', params)
            return Rows([{'raw': self.db.blobs.get(row['digest'])}] if row else [])
        if sql.startswith('SELECT b.raw FROM collector_attachments a JOIN collector_blobs b '):
            row = self.db.one('SELECT b.digest FROM collector_attachments a JOIN collector_blobs b '
                              'ON a.raw_digest=b.digest WHERE a.file_unique_id=%s '
                              'ORDER BY a.raw_available_at,a.message_id LIMIT 1', params)
            return Rows([{'raw': self.db.blobs.get(row['digest'])}] if row else [])
        if sql == 'INSERT OR IGNORE INTO collector_blobs VALUES (?,?)':
            digest = self.db.put_blob(params[1])
            if digest != params[0]:
                raise Conflict('reader attachment digest changed')
            self.db.execute('INSERT INTO collector_blobs(digest) VALUES(%s) ON DUPLICATE KEY UPDATE digest=digest',
                            (digest,))
            return Rows([])
        if sql.startswith('INSERT OR IGNORE INTO '):
            sql = sql.replace('INSERT OR IGNORE INTO ', 'INSERT INTO ', 1)
            table = sql.split()[2].split('(')[0]
            column = self._duplicates[table]
            sql += f' ON DUPLICATE KEY UPDATE {column}={column}'
        elif sql.startswith('INSERT OR REPLACE INTO '):
            sql = sql.replace('INSERT OR REPLACE INTO ', 'REPLACE INTO ', 1)
        elif sql.startswith('INSERT INTO collector_validation_history VALUES '):
            sql = sql.replace('history VALUES', 'history(chat_id,message_id,revision,observed_at,validation) VALUES')
        sql = sql.replace('MAX(last_update_id,', 'GREATEST(last_update_id,').replace('?', '%s')
        if sql.startswith('SELECT '):
            return Rows(self.db.rows(sql, params))
        self.db.execute(sql, params)
        return Rows([])


def evidence_reader(db, chat_id, *, clock=time):
    module = reader_module(db.settings, 'btc_evidence_reader')

    class NodeEvidenceReader(module.EvidenceReader):
        def __init__(self):
            self.allowed_chat_id = chat_id
            self.allowed_sender_ids = None
            self.clock = clock
            self.lock = RLock()
            self.db = ReaderConnection(db)
            self.in_batch = False

        @contextmanager
        def _tx(self):
            if self.in_batch:
                yield self.db
            else:
                with db.writer(timeout=600), db.transaction():
                    yield self.db

        def close(self):
            # The caller owns the one MySQL connection.
            pass

    return NodeEvidenceReader()


def _configured(db):
    if not native(db):
        raise Conflict('native Docker reader has not been cut over')
    if (db.metadata('writer') or {}).get('owner') != 'DOCKER':
        raise Conflict('native reader requires the active Docker writer')
    config = db.one('SELECT * FROM reader_config WHERE singleton_id=1')
    if not config:
        raise Conflict('native Docker reader has no channel configuration')
    return config


def status(db):
    _configured(db)
    state = db.one('SELECT last_update_id,last_success_at_utc,last_error_code FROM sync_state WHERE singleton_id=1')
    if state is None:
        raise Conflict('native Docker reader has no durable cursor')
    return {'backend': NATIVE, **state}


def observations(settings):
    db = Database(settings)
    try:
        db.execute('START TRANSACTION READ ONLY')
        config = _configured(db)
        return evidence_reader(db, config['chat_id']).observations(latest_only=True, asset='all')
    finally:
        db.connection.rollback()
        db.close()


class PolledBatch:
    def __init__(self, offset, updates):
        self.offset = offset
        self.updates = updates

    def get_updates(self, *, offset):
        if offset != self.offset:
            raise Conflict('reader cursor changed during the Telegram poll')
        return self.updates

    def download_document(self, _file_id):
        raise Conflict('reader download must run after the cursor commits')


class BatchEvidence:
    def __init__(self, reader):
        self.reader = reader

    def ingest_update(self, update):
        return self.reader.ingest_update(update)

    def process_pending(self, _download):
        # Downloads run after the outer MySQL transaction commits its cursor.
        pass


def sync(db, source):
    """Poll once, commit the raw receipts and cursor together, then download."""
    config = _configured(db)
    store_module = reader_module(db.settings, 'store')
    models = reader_module(db.settings, 'models')
    engine = reader_module(db.settings, 'sync_engine')

    class NodeMessageStore(store_module.MessageStore):
        def __init__(self):
            self._connection = ReaderConnection(db)

        def get_last_update_id(self):
            return db.one('SELECT last_update_id FROM sync_state WHERE singleton_id=1')['last_update_id']

    store = NodeMessageStore()
    reader = evidence_reader(db, config['chat_id'])
    # This dedicated lock spans network I/O. The node writer lock only spans
    # the durable changes, so analysis can proceed during a slow download.
    lock = 'btc-reader:' + db.settings['mysql']['database']
    if db.one('SELECT GET_LOCK(%s,0) AS acquired', (lock,))['acquired'] != 1:
        raise Conflict('another Docker reader poll is in flight')
    try:
        channel = source.get_chat(config['chat_id'])
        if (source.get_me().get('id') != config['reader_bot_id']
                or channel.get('type') != 'channel' or channel.get('id') != config['chat_id']):
            raise Conflict('reader credential or Telegram channel differs from the saved binding')
        cursor = store.get_last_update_id()
        offset = cursor + 1 if cursor else None
        updates = source.get_updates(offset=offset)
        with db.writer(timeout=600), db.transaction():
            reader.in_batch = True
            result = engine.synchronize(store=store, source=PolledBatch(offset, updates),
                                        config=models.ReaderConfig(config['chat_id'], config['reader_bot_id'],
                                                                   config['configured_at_utc']),
                                        evidence_reader=BatchEvidence(reader))
        reader.in_batch = False
        reader.process_pending(source.download_document)
        return result
    except Exception as error:
        try:
            with db.writer(timeout=15), db.transaction():
                db.execute('UPDATE sync_state SET last_error_code=%s WHERE singleton_id=1',
                           (type(error).__name__,))
        except Exception as status_error:
            error.add_note('reader failure status could not be persisted: ' + type(status_error).__name__)
        raise
    finally:
        db.connection.rollback()
        db.one('SELECT RELEASE_LOCK(%s) AS released', (lock,))


def poll(settings, token):
    from .reader_prices import project
    from .scheduler import observe_reader

    api = reader_module(settings, 'bot_client').BotAPI(token)
    db = Database(settings)
    try:
        db.connection.autocommit(True)
        result = sync(db, api)
        with db.writer(timeout=600), db.transaction():
            prices = project(db)
            observe_reader(db, prices)
        return {'backend': NATIVE, 'scanned_updates': result.scanned_updates, 'changed_messages': result.changed_messages,
                'last_update_id': result.last_update_id, 'prices_processed': prices['processed']}
    finally:
        db.close()
