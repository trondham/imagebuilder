"""main() end to end: every exit path must still clean up.

These drive main() with a stand-in BuildFunctions, so they cover the wiring
between the pieces rather than the pieces themselves.
"""
import tempfile

import pytest

import image_builder.imagebuilder as ib

from conftest import FULL_OPENRC


@pytest.fixture
def run_build(clean_env, monkeypatch):
    """Runs main() for a build, returning (exit code, propagated exception, calls)."""

    def run(packer=lambda: 0, keygen_ok=True, manifest='img-uuid', argv=None):
        calls = []

        class FakeBuild:
            def __init__(self, *a, **k):
                self.tmp_dir = tempfile.mkdtemp(prefix='mainflow-')
                self.image_name = 'img'

            def find_template(self):
                return '/tmp/template.pkr.hcl'

            def find_network_id(self, name):
                return 'net-1'

            def run_packer_init(self, template):
                return 0

            def create_security_group(self):
                calls.append('sg_created')
                return 'sg-name', 'sg-1'

            def create_keypairs(self):
                if not keygen_ok:
                    return None, None
                calls.append('kp_created')
                return 'kp-name', 'kp-1'

            def run_packer(self, *a):
                return packer()

            def parse_manifest(self):
                return manifest

            def download_image(self, artifact_id):
                return 0

            def delete_image(self, image_id):
                return True

            def cleanup(self, secgroup_id, keypair_id):
                calls.append('cleanup(sg=%s kp=%s)' % (secgroup_id, keypair_id))

        for key, value in FULL_OPENRC.items():
            clean_env.setenv(key, value)
        clean_env.setenv('IB_TEMPLATE_DIR', '/tmp')
        clean_env.setenv('IB_DOWNLOAD_DIR', '/tmp')
        monkeypatch.setattr(ib, 'BuildFunctions', FakeBuild)
        monkeypatch.setattr(ib.ImageBuilder, 'auth', staticmethod(lambda rc: object()))
        monkeypatch.setattr('sys.argv', argv or
                            ['imagebuilder', 'build', '-n', 'img',
                             '-a', 'bgo-default-1', '-s', 'src', '-u', 'almalinux'])

        code, raised = None, None
        try:
            ib.main()
        except SystemExit as exc:
            code = exc.code
        except BaseException as exc:            # noqa: BLE001
            raised = exc
        return code, raised, calls

    return run


def cleaned(calls):
    return [c for c in calls if c.startswith('cleanup')]


def test_successful_build_exits_zero_and_cleans_up(run_build):
    code, raised, calls = run_build()
    assert code == 0 and raised is None
    assert cleaned(calls) == ['cleanup(sg=sg-1 kp=kp-1)']


def test_failed_packer_run_exits_one_and_cleans_up(run_build):
    code, raised, calls = run_build(packer=lambda: 1)
    assert code == 1
    assert cleaned(calls)


def test_interrupt_during_build_still_cleans_up(run_build):
    """The case that used to orphan a security group and a keypair."""
    def interrupt():
        raise KeyboardInterrupt()

    code, raised, calls = run_build(packer=interrupt)
    assert cleaned(calls) == ['cleanup(sg=sg-1 kp=kp-1)']
    assert isinstance(raised, KeyboardInterrupt)     # and is not swallowed


def test_unexpected_exception_still_cleans_up(run_build):
    def boom():
        raise RuntimeError('packer blew up')

    code, raised, calls = run_build(packer=boom)
    assert cleaned(calls)
    assert isinstance(raised, RuntimeError)


def test_keygen_failure_exits_before_packer_and_cleans_up(run_build):
    code, raised, calls = run_build(keygen_ok=False)
    assert code == 1
    assert 'kp_created' not in calls
    assert cleaned(calls) == ['cleanup(sg=sg-1 kp=None)']


def test_unusable_manifest_fails_the_build(run_build):
    code, raised, calls = run_build(manifest=None)
    assert code == 1
    assert cleaned(calls)
