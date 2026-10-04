"""Local administration. No administrative operation is exposed as an MCP tool."""
from pathlib import Path

from .catalog import Catalog
from .codec import Conflict, millis
from .database import Database
from .market_store import import_sqlite


def watermark(db):
    result=db.one('SELECT COUNT(*) AS operations,COALESCE(MAX(finished_ms),0) AS finished_ms FROM operations')
    # Reader checkpoints are independent of model commands. A fresh Telegram
    # cursor must invalidate an older host handoff even if no report was run.
    result['reader_checkpoint']=db.one("SELECT digest FROM node_metadata WHERE name='reader.checkpoint'")
    return result


def run(settings, command, args):
    db = Database(settings)
    try:
        if command in {'backup','restore-database'}:
            from . import backup
            return backup.create(db,args.directory) if command=='backup' else backup.restore(db,args.directory)
        with db.writer():
            db.initialize()
            if command == 'import':
                with db.transaction():
                    root = Path(args.root)
                    catalog = Catalog(db)
                    imported = catalog.sync(args.namespace, root)
                    checked = catalog.compare(args.namespace, root)
                    if not checked['equal']:
                        raise Conflict('source changed during migration')
                    market = root / 'market_observation.sqlite3'
                    counts = import_sqlite(db, market) if args.namespace == 'strategy' and market.is_file() else {}
                    db.metadata('import:' + args.namespace, {'checked_ms': millis(), 'root': str(root),
                                'documents': len(imported['changed']), 'market_rows': counts}, write=True)
                    return {'status': 'IMPORTED_AND_COMPARED', 'namespace': args.namespace,
                            'documents': len(imported['changed']), 'databases': imported['sqlite'], 'market_rows': counts}
            if command == 'compare':
                with db.transaction():
                    return Catalog(db).compare(args.namespace, args.root)
            if command == 'restore':
                target = Path(args.root)
                if target.exists() and any(target.iterdir()):
                    raise ValueError('restore target must be empty; never overwrite live state with a snapshot')
                with db.transaction():
                    result = Catalog(db).restore(args.namespace, target, hardlink=True)
                    if args.namespace == 'strategy' and (target / 'market_observation.sqlite3').is_file():
                        from .market_store import export_sqlite
                        result['current_mysql_protocol'] = export_sqlite(db, target / 'market_observation.sqlite3')
                        db.metadata('rollback.export', {'root':str(target.resolve()),'watermark':watermark(db),
                                    'at_ms':millis()}, write=True)
                    return result
            if command == 'project':
                from .execution import project_state
                with db.transaction():
                    result = project_state(db, settings, args.release)
                    return {'changed': sum(1 for item in result['objects'] if item['changed']),
                            'visited': len(result['objects']), 'facts_match': result.get('facts_match'),
                            'history': result.get('history'), 'reader_receipts': result.get('reader_receipts')}
            if command == 'writer':
                with db.transaction():
                    previous = db.metadata('writer')
                    if db.one("SELECT operation_id FROM operations WHERE status<>'COMMITTED' LIMIT 1"):
                        raise Conflict('reconcile pending operations before changing the writer')
                    if args.owner == 'DOCKER' and not db.metadata('import:strategy'):
                        raise Conflict('import and compare state before switching writer')
                    if args.owner == 'HOST' and (previous or {}).get('owner') == 'DOCKER':
                        exported = db.metadata('rollback.export')
                        if not exported or exported['watermark'] != watermark(db):
                            raise Conflict('export the latest business and delivery state into a fresh rollback directory before host handoff')
                    value = {'owner': args.owner, 'changed_ms': millis(), 'previous': previous}
                    db.metadata('writer', value, write=True)
                    return value
    finally:
        db.close()
