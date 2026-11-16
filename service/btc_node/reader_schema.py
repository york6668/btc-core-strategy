"""Storage for the Docker reader, including its durable Telegram cursor."""

DDL = (
    """CREATE TABLE IF NOT EXISTS reader_config (
        singleton_id TINYINT PRIMARY KEY, chat_id BIGINT NOT NULL,
        reader_bot_id BIGINT NOT NULL, configured_at_utc VARCHAR(40) NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sync_state (
        singleton_id TINYINT PRIMARY KEY, last_update_id BIGINT NOT NULL,
        last_success_at_utc VARCHAR(40), last_error_code VARCHAR(191))""",
    """CREATE TABLE IF NOT EXISTS messages (
        chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL, sender_id BIGINT,
        sender_is_bot TINYINT NOT NULL, sent_at_utc VARCHAR(40), edited_at_utc VARCHAR(40),
        current_text TEXT NOT NULL, content_kind VARCHAR(32) NOT NULL,
        first_seen_at_utc VARCHAR(40) NOT NULL, last_seen_at_utc VARCHAR(40) NOT NULL,
        PRIMARY KEY(chat_id,message_id))""",
    """CREATE TABLE IF NOT EXISTS message_revisions (
        revision_id BIGINT AUTO_INCREMENT PRIMARY KEY, chat_id BIGINT NOT NULL,
        message_id BIGINT NOT NULL, event_type VARCHAR(16) NOT NULL,
        observed_at_utc VARCHAR(40) NOT NULL, sender_id BIGINT, sender_is_bot TINYINT NOT NULL,
        telegram_edited_at_utc VARCHAR(40), text TEXT NOT NULL, content_kind VARCHAR(32) NOT NULL,
        content_fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        UNIQUE KEY revision_identity(chat_id,message_id,event_type,content_fingerprint))""",
    """CREATE TABLE IF NOT EXISTS collector_updates (
        update_id BIGINT PRIMARY KEY, digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        status VARCHAR(32) NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS collector_update_conflicts (
        update_id BIGINT NOT NULL, digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        content LONGTEXT NOT NULL, PRIMARY KEY(update_id,digest))""",
    """CREATE TABLE IF NOT EXISTS collector_validation_history (
        validation_id BIGINT AUTO_INCREMENT PRIMARY KEY, chat_id BIGINT NOT NULL,
        message_id BIGINT NOT NULL, revision CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        observed_at DOUBLE NOT NULL, validation LONGTEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS collector_messages (
        chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL, version_time BIGINT NOT NULL,
        update_id BIGINT NOT NULL, text TEXT,
        revision CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        PRIMARY KEY(chat_id,message_id))""",
    """CREATE TABLE IF NOT EXISTS collector_revisions (
        chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL,
        revision CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        content LONGTEXT NOT NULL, PRIMARY KEY(chat_id,message_id,revision))""",
    """CREATE TABLE IF NOT EXISTS collector_blobs (
        digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
        FOREIGN KEY(digest) REFERENCES blobs(digest))""",
    """CREATE TABLE IF NOT EXISTS collector_attachments (
        chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL,
        revision CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        parent_id BIGINT, file_id TEXT NOT NULL, file_unique_id TEXT COLLATE utf8mb4_bin,
        declared_bytes BIGINT, received_at DOUBLE NOT NULL,
        raw_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin, validation LONGTEXT,
        download_status VARCHAR(32) NOT NULL DEFAULT 'pending', download_error VARCHAR(64),
        download_attempts BIGINT NOT NULL DEFAULT 0, download_retry_at DOUBLE NOT NULL DEFAULT 0,
        raw_available_at DOUBLE, PRIMARY KEY(chat_id,message_id,revision),
        INDEX collector_parent(chat_id,parent_id,message_id),
        INDEX collector_retry(chat_id,download_status,download_retry_at),
        INDEX collector_raw(raw_digest), FOREIGN KEY(raw_digest) REFERENCES collector_blobs(digest))""",
    """CREATE TABLE IF NOT EXISTS collector_download_failures (
        chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL,
        revision CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        attempt BIGINT NOT NULL, failed_at DOUBLE NOT NULL, detail LONGTEXT NOT NULL,
        PRIMARY KEY(chat_id,message_id,revision,attempt))""",
)

# The order keeps the archival cursor and audit tables together and ensures
# references exist before the node is allowed to poll with that cursor.
ARCHIVED = {
    'messages.sqlite3': ('reader_config', 'sync_state', 'messages', 'message_revisions'),
    'collector_evidence.sqlite3': (
        'collector_updates', 'collector_update_conflicts', 'collector_validation_history',
        'collector_messages', 'collector_revisions', 'collector_blobs',
        'collector_attachments', 'collector_download_failures',
    ),
}
