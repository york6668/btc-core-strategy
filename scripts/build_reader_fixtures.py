"""Construct protocol replay documents in a caller-owned temporary directory."""
import json
from pathlib import Path


def build(directory):
    from collector_replay_fixture import build_case
    import btc_evidence
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    valid = build_case('valid', btc_evidence)
    for name in ('valid', 'missing'):
        text = valid['text'] if name == 'valid' else 'Synthetic missing public evidence; offline replay only.'
        bundle = valid['bundle'] if name == 'valid' else btc_evidence.build_bundle({}, text, 'fixture:missing', '2026-09-04T06:00:10+00:00')
        (directory / (name + '.txt')).write_text(text, encoding='utf-8')
        (directory / (name + '.json')).write_bytes(btc_evidence.encode_bundle(bundle))
    messages = []
    for number in (201, 202):
        post = {'message_id': number, 'chat': {'id': -1000000000001, 'type': 'channel'}, 'date': 1788501600 + number - 201}
        if number == 201:
            post['text'] = valid['text']
        else:
            post.update(reply_to_message={'message_id': 201}, document={'file_id': 'offline-fixture',
                'file_unique_id': 'offline-unique', 'file_name': 'btc-evidence.json',
                'mime_type': 'application/json', 'file_size': len(valid['encoded'])})
        messages.append({'update_id': number - 100, 'channel_post': post})
    (directory / 'telegram_updates.json').write_text(json.dumps(dict(zip(('text_update', 'document_update'), messages))), encoding='utf-8')
