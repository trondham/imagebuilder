import hashlib
import logging
import shutil
import tempfile

log = logging.getLogger(__name__)

class Helpers(object):

    @staticmethod
    def clean_tmp_files(tmp_dir):
        # Also called from a finally block, so failing to remove a scratch
        # directory must not be what the user ends up seeing
        log.info('Removing temporary directory with content...')
        shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def make_tmp_dir():
        log.info('Creating a directory for temporary files...')
        tmp_dir = tempfile.mkdtemp(prefix='imagebuilder-')
        return tmp_dir

    @staticmethod
    def log_subprocess_output(pipe):
        # Decoded, not repr'd: this is the build log the user watches, and
        # every line of it used to arrive looking like b'...\n'
        for line in iter(pipe.readline, b''):
            log.info(line.decode('utf-8', errors='replace').rstrip())

    @staticmethod
    def valid_digest(digest):
        """Whether hashlib knows this digest name

        Used to reject a bad -t before spending a download on it.
        """
        try:
            hashlib.new(digest.lower())
        except (ValueError, TypeError):
            return False
        return True

    @staticmethod
    def checksum_file(file_path, digest='sha256', chunk_size=65536):
        """Read the file in small pieces, so as to prevent failures to read
        particularly large files.  Also ensures memory usage is kept to a
        minimum. Testing shows default is a pretty good size."""
        assert isinstance(chunk_size, int) and chunk_size > 0
        # hashlib.new() takes any algorithm it supports and raises a clear
        # ValueError naming a bad one. The hand-written if/elif ladder this
        # replaces silently left unknown names as a string and then died with
        # AttributeError on the first update()
        hasher = hashlib.new(digest.lower())
        #pylint: disable=invalid-name
        with open(file_path, 'rb') as f:
            for block in iter(lambda: f.read(chunk_size), b''):
                hasher.update(block)
        checksum = hasher.hexdigest()
        log.debug("hexdigest of %s is %s", file_path, checksum)
        return checksum
