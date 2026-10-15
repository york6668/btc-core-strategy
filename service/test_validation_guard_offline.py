"""Offline fault injection for the disposable Docker admission boundary."""
import datetime as dt
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest

import validation_guard as guard


class FakeDocker:
    def __init__(self):
        self.now = dt.datetime(2026, 10, 14, 8, 0, tzinfo=dt.timezone.utc)
        self.names = ''
        self.volume_names = ''
        self.network_names = ''
        self.container_project_labels = ''
        self.network_project_labels = ''
        self.claim = False
        self.created = self.now
        self.available = 6 * guard.GIB
        self.swap_free = guard.GIB
        self.oom_events = ''
        self.project_containers = ''
        self.project_networks = ''
        self.project_volumes = ''
        self.volume_attached = ''
        self.volume_record = None
        self.daemon_id = 'synthetic-daemon-id'
        self.fail_rm = False
        self.calls = []

    def __call__(self, args, **_kwargs):
        self.calls.append(tuple(args[1:]))
        command = args[1]
        code, output = 0, ''
        if command == 'create':
            if self.claim:
                code = 1
            else:
                self.claim = True
                output = 'synthetic-claim-id'
        elif command == 'image':
            pass
        elif command == 'inspect':
            if not self.claim:
                code = 1
            else:
                output = json.dumps({'Created': self.created.isoformat(),
                                     'Config': {'Labels': {'btc.project': 'btc-test-owner'}}})
        elif command == 'rm':
            if self.fail_rm:
                code = 1
            else:
                self.claim = False
        elif command == 'ps':
            if '{{.Label "com.docker.compose.project"}}' in args:
                output = self.container_project_labels
            elif '--filter' in args:
                output = self.volume_attached if any(part.startswith('volume=') for part in args) else self.project_containers
            else:
                output = self.names
        elif command == 'network':
            if '{{.Label "com.docker.compose.project"}}' in args:
                output = self.network_project_labels
            else:
                output = self.project_networks if '--filter' in args else self.network_names
        elif command == 'volume':
            if args[2] == 'inspect':
                if self.volume_record is None:
                    code = 1
                else:
                    output = json.dumps(self.volume_record)
            else:
                output = self.project_volumes if '--filter' in args else self.volume_names
        elif command == 'info':
            output = json.dumps({'MemTotal': 8 * guard.GIB, 'NCPU': 10, 'ID': self.daemon_id})
        elif command == 'start':
            output = ('MemTotal: 8388608 kB\nMemAvailable: ' + str(self.available // 1024) +
                      ' kB\nSwapTotal: 1048576 kB\nSwapFree: ' +
                      str(self.swap_free // 1024) + ' kB\n')
        elif command == 'events':
            output = self.oom_events
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, code, output, 'synthetic failure' if code else '')


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fake = FakeDocker()
        self.path = Path(self.temp.name) / 'claim.lock'

    def make(self, fake=None, **kwargs):
        fake = fake or self.fake
        return guard.ValidationGuard('docker', stack_limit_bytes=2 * guard.GIB,
                                     run=fake, lock_path=self.path, now=lambda: fake.now,
                                     platform_name='Linux', **kwargs)

    def retained_manifest(self):
        project = 'btc-adoption-frozen123'
        name = project + '_mysql'
        mountpoint = '/var/lib/docker/volumes/' + name + '/_data'
        entry = {'name': name, 'project': project, 'driver': 'local',
                 'created_at': '2026-10-14T06:59:31Z', 'mountpoint': mountpoint,
                 'state': 'PRESERVED'}
        self.fake.volume_names = name
        self.fake.project_volumes = name
        self.fake.volume_record = {'Name': name, 'Driver': 'local',
                                   'CreatedAt': entry['created_at'],
                                   'Mountpoint': mountpoint,
                                   'Labels': {'com.docker.compose.project': project,
                                              'com.docker.compose.volume': 'mysql'}}
        path = Path(self.temp.name) / 'preserved-volumes.json'
        path.write_text(json.dumps({'schema': guard.PRESERVED_VOLUME_SCHEMA,
                                    'daemon_id': self.fake.daemon_id,
                                    'volumes': [entry]}))
        return path

    def test_local_concurrent_claim_refused(self):
        first = self.make()
        with first:
            with self.assertRaisesRegex(guard.AdmissionError, 'another local'):
                with self.make():
                    pass
        self.assertFalse(self.fake.claim)

    def test_remote_claim_and_stale_lock_fail_closed(self):
        self.fake.claim = True
        self.fake.created -= dt.timedelta(hours=2)
        with self.assertRaisesRegex(guard.AdmissionError, 'stale'):
            with self.make():
                pass
        self.assertTrue(self.fake.claim)
        self.assertFalse(any(call[0] == 'rm' for call in self.fake.calls))
        self.fake.claim = False
        with self.make():
            pass  # local flock was released after the failed claim

    def test_same_daemon_blocks_different_working_directory(self):
        second_path = Path(self.temp.name) / 'another-worktree.lock'
        with self.make():
            second = guard.ValidationGuard('docker', stack_limit_bytes=2*guard.GIB,
                run=self.fake, lock_path=second_path, now=lambda:self.fake.now,
                platform_name='Linux', project='btc-test-another')
            with self.assertRaisesRegex(guard.AdmissionError, 'occupied for btc-test-owner'):
                with second:
                    pass
        self.assertFalse(self.fake.claim)

    def test_retained_stack_refuses_new_instance(self):
        self.fake.names = 'btc-adoption-old-core-1\nbtc-analysis-core-1'
        with self.assertRaisesRegex(guard.AdmissionError, 'retained'):
            with self.make():
                pass
        self.assertFalse(self.fake.claim)

    def test_renamed_stack_still_refused_by_compose_project_label(self):
        self.fake.names = 'renamed-core-1'
        self.fake.network_names = 'renamed-default'
        self.fake.container_project_labels = 'btc-adoption-original'
        with self.assertRaisesRegex(guard.AdmissionError, 'btc-adoption-original'):
            with self.make():
                pass
        self.assertFalse(self.fake.claim)
        self.fake.container_project_labels = ''
        self.fake.network_project_labels = 'btc-test-original'
        with self.assertRaisesRegex(guard.AdmissionError, 'btc-test-original'):
            with self.make():
                pass
        self.assertFalse(self.fake.claim)

    def test_retained_volume_alone_refuses_new_instance(self):
        self.fake.volume_names = 'btc-adoption-old_mysql'
        with self.assertRaisesRegex(guard.AdmissionError, 'retained BTC validation resources'):
            with self.make():
                pass
        self.assertFalse(self.fake.claim)

    def test_exact_preserved_volume_still_requires_all_other_gates(self):
        path = self.retained_manifest()
        with self.make(preserved_volume_manifest=path):
            pass
        self.assertFalse(self.fake.claim)
        self.assertTrue(any(call[:2] == ('volume', 'inspect') for call in self.fake.calls))
        self.assertTrue(any(call[0] == 'start' for call in self.fake.calls))
        self.fake.available = 3 * guard.GIB
        with self.assertRaisesRegex(guard.AdmissionError, 'memory admission denied'):
            with self.make(preserved_volume_manifest=path):
                pass

    def test_preserved_volume_drift_or_live_owner_blocks(self):
        path = self.retained_manifest()
        def check(message):
            with self.assertRaisesRegex(guard.AdmissionError, message):
                with self.make(preserved_volume_manifest=path):
                    pass
            self.assertFalse(self.fake.claim)
        self.fake.names = 'btc-adoption-frozen123-mysql-1'
        check('retained BTC validation resources')
        self.fake.names = ''
        self.fake.network_names = 'btc-adoption-frozen123_default'
        check('retained BTC validation resources')
        self.fake.network_names = ''
        self.fake.volume_attached = 'stopped-owner-id'
        check('still has a container or network')
        self.fake.volume_attached = ''
        self.fake.project_networks = 'empty-network-id'
        check('still has a container or network')
        self.fake.project_networks = ''
        self.fake.project_containers = 'renamed-owner-id'
        check('still has a container or network')
        self.fake.project_containers = ''
        self.fake.project_volumes += '\nbtc-adoption-frozen123_other'
        check('another volume')
        self.fake.project_volumes = 'btc-adoption-frozen123_mysql'
        self.fake.volume_names += '\nbtc-test-unknown_mysql'
        check('inventory changed')
        self.fake.volume_names = 'btc-adoption-frozen123_mysql'
        self.fake.daemon_id = 'different-daemon-id'
        check('daemon identity changed')
        self.fake.daemon_id = 'synthetic-daemon-id'
        self.fake.volume_record['CreatedAt'] = 'recreated-at-new-time'
        check('identity changed')
        self.fake.volume_record['CreatedAt'] = '2026-10-14T06:59:31Z'
        self.fake.volume_record['Mountpoint'] = '/different/mount'
        check('identity changed')
        self.fake.volume_record['Mountpoint'] = '/var/lib/docker/volumes/btc-adoption-frozen123_mysql/_data'
        self.fake.volume_record['Labels']['com.docker.compose.project'] = 'btc-test-other'
        check('identity changed')

    def test_preserved_manifest_is_strict_and_not_a_prefix_exemption(self):
        path = self.retained_manifest()
        value = json.loads(path.read_text())
        value['volumes'][0]['name'] = 'btc-adoption-frozen123_other'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(guard.AdmissionError, 'invalid preserved-volume identity'):
            self.make(preserved_volume_manifest=path)
        value['volumes'][0]['name'] = ['btc-adoption-frozen123_mysql']
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(guard.AdmissionError, 'invalid preserved-volume identity'):
            self.make(preserved_volume_manifest=path)
        value['volumes'][0]['name'] = 'btc-adoption-frozen123_mysql'
        value['volumes'][0]['created_at'] = 'changed-in-registry'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(guard.AdmissionError, 'identity changed'):
            with self.make(preserved_volume_manifest=path):
                pass
        path.write_text('{broken JSON')
        with self.assertRaisesRegex(guard.AdmissionError, 'cannot be read'):
            self.make(preserved_volume_manifest=path)

    def test_memory_and_recent_oom_refuse_admission(self):
        self.fake.available = 3 * guard.GIB
        with self.assertRaisesRegex(guard.AdmissionError, 'memory admission denied'):
            with self.make():
                pass
        self.assertFalse(self.fake.claim)
        self.fake.available = 6 * guard.GIB
        self.fake.oom_events = '2026-10-14T07:30:31Z'
        with self.assertRaisesRegex(guard.AdmissionError, 'OOM within'):
            with self.make():
                pass

    def test_desktop_kernel_oom_detected_when_docker_events_are_empty(self):
        log = Path(self.temp.name) / 'init.log'
        log.write_text(json.dumps({'time': '2026-10-14T07:30:31Z',
                                   'msg': 'oom-kill:constraint=CONSTRAINT_NONE,global_oom,task=mysqld'})+'\n')
        self.assertIsNotNone(guard.recent_vm_global_oom(self.temp.name,
            self.fake.now-dt.timedelta(hours=1)))
        with self.assertRaisesRegex(guard.AdmissionError, 'kernel reported global OOM'):
            with guard.ValidationGuard('docker', stack_limit_bytes=2*guard.GIB,
                    run=self.fake, lock_path=self.path, now=lambda:self.fake.now,
                    platform_name='Darwin', vm_log_dir=self.temp.name):
                pass
        self.assertFalse(self.fake.claim)

    def test_cleanup_failure_keeps_daemon_claim(self):
        with self.assertRaisesRegex(guard.AdmissionError, 'claim retained'):
            with self.make() as held:
                held.project_started = True
                self.fake.project_containers = 'some-id'
                with self.assertRaisesRegex(guard.AdmissionError, 'cleanup incomplete'):
                    held.verify_project_gone('btc-adoption-example')
        self.assertTrue(self.fake.claim)
        self.fake.claim = False
        with self.make():
            pass  # local flock is released; daemon claim is the failure gate

    def test_successful_cleanup_releases_claim(self):
        with self.make() as held:
            held.project_started = True
            held.verify_project_gone('btc-adoption-example')
        self.assertFalse(self.fake.claim)

    def test_interruption_preserves_unverified_claim(self):
        restore = guard.install_interrupt_handlers()
        try:
            with self.assertRaisesRegex(guard.AdmissionError, 'claim retained'):
                with self.make() as held:
                    held.project_started = True
                    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        finally:
            restore()
        self.assertTrue(self.fake.claim)

    def test_cleanup_command_failure_cannot_be_marked_clean(self):
        self.fake.project_networks = 'network-id'
        with self.assertRaisesRegex(guard.AdmissionError, 'claim retained'):
            with self.make() as held:
                held.project_started = True
                with self.assertRaisesRegex(guard.AdmissionError, 'cleanup incomplete'):
                    held.verify_project_gone('btc-test-example')
        self.assertTrue(self.fake.claim)

    def test_cleanup_rejects_retained_volume(self):
        self.fake.project_volumes = 'volume-id'
        with self.assertRaisesRegex(guard.AdmissionError, 'claim retained'):
            with self.make() as held:
                held.project_started = True
                with self.assertRaisesRegex(guard.AdmissionError, 'cleanup incomplete'):
                    held.verify_project_gone('btc-test-example')
        self.assertTrue(self.fake.claim)

    def test_compose_down_failure_and_timeout_retain_claim(self):
        for failure in ('nonzero', 'timeout'):
            with self.subTest(failure=failure):
                self.fake.claim = False
                with self.assertRaisesRegex(guard.AdmissionError, 'claim retained'):
                    with self.make() as held:
                        held.project_started = True
                        def failing_run(args, **_kwargs):
                            self.assertEqual(args[-2:], ['down', '--remove-orphans'])
                            self.assertNotIn('--volumes', args)
                            if failure == 'timeout':
                                raise subprocess.TimeoutExpired(args, 90)
                            return subprocess.CompletedProcess(args, 1, '', 'synthetic down failure')
                        with self.assertRaises(guard.AdmissionError):
                            guard.cleanup_compose(['docker', 'compose', '-p', 'btc-test-example'],
                                                  'btc-test-example', held, run=failing_run)
                self.assertTrue(self.fake.claim)

    def test_compose_down_success_requires_no_containers_or_networks(self):
        with self.make() as held:
            held.project_started = True
            def success(args, **_kwargs):
                self.assertEqual(args[-2:], ['down', '--remove-orphans'])
                return subprocess.CompletedProcess(args, 0, '', '')
            guard.cleanup_compose(['docker', 'compose', '-p', 'btc-test-example'],
                                  'btc-test-example', held, run=success)
            self.assertTrue(held.cleaned)
        self.assertFalse(self.fake.claim)


if __name__ == '__main__':
    unittest.main()
