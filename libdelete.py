"""
Library and helper functions for the delete(1) suite of tools, including
delete, expunge, lsdel, purge, and undelete.
"""

import errno
import glob
import logging
import os
import re
import six
import sys
import stat

WILDCARDS_RE = re.compile('([*?[])')
KILO = 1024

logger = logging.getLogger('libdelete')
have_AFS = True

try:
    import afs.fs
except ImportError:
    logger.warn("AFS support unavailable")
    have_AFS = False

class DeleteError(Exception):
    pass

def chunks(seq, size):
    """
    Break a sequence up into size chunks
    """
    return (seq[pos:pos + size] for pos in six.moves.range(0, len(seq), size))

def format_columns(items, singlecol=False, width=80):
    """
    Pretty-print in optional multi-column format, with padding/spread.
    """
    if singlecol:
        return "\n".join(items)
    # The old code printed column-first, not row-first.
    # I can't be convinced to care.
    if len(items) < 1:
        return ""
    col_width = max(len(x) for x in items) + 2
    if col_width > width:
        return "\n".join(items)
    n_cols = width // col_width
    padding = (80 - (n_cols * col_width)) // n_cols
    rv = []
    for c in chunks(items, n_cols):
        rv.append("".join(item.ljust(col_width + padding) for item in c))
    return "\n".join(rv)

def is_mountpoint(path):
    if os.path.ismount(path):
        return True
    if have_AFS and afs.fs.inafs(os.path.abspath(path)):
        afs.fs.whichcell(path)
        try:
            return afs.fs.lsmount(path) is not None
        except OSError as e:
            logger.debug("Got exception while checking mount point: %s", e)
    return False

def has_wildcards(string):
    return WILDCARDS_RE.search(string) is not None

def is_deleted(path):
    """
    Return True if the file has been 'deleted' by delete(1)
    """
    return os.path.basename(path).startswith('.#')

def dir_listing(path):
    """
    A directory listing with the full path.
    """
    return [os.path.join(path, x) for x in os.listdir(path)]

def empty_directory(path):
    """
    Return True if the directory is "empty" (that is, any entries
    in it have been deleted)
    """
    return all(is_deleted(x) for x in dir_listing(path))

def relpath(path):
    """
    For relative paths that begin with '.', strip off the leading
    stuff.
    """
    return path[2:] if path.startswith('./') else path

def undeleted_name(path):
    """
    Return the undeleted name of a file.  Only the last component
    is changed.  If it's in a chain of deleted directories, those
    are still printed with the leading '.#' for compatibility.
    """
    parts = os.path.split(path)
    if parts[1].startswith('.#'):
        return os.path.join(parts[0],
                            parts[1][2:])
    else:
        return path

def n_days_old(path, n):
    if n < 0:
        raise ValueError("n must not be negative")
    if n == 0:
        # All extant files are, by definition, 0 days old
        return True
    mtime = os.path.getmtime(path)
    logger.debug("%s modified %d sec ago", path, mtime)
    return ((time.time() - mtime) >= (86400 * n))

def escape_meta(path):
    return WILDCARDS_RE.sub(r'[\1]', path)

def to_kb(size):
    return int(round(float(size) / KILO))

def find_deleted_files(file_or_pattern, follow_links=False,
                       follow_mounts=False, recurse_undeleted_subdirs=None,
                       recurse_deleted_subdirs=None, n_days=0):

    logger.debug("find_deleted_files(%s, links=%s, mounts=%s, recurse_un=%s, recurse_del=%s, ndays=%s)",
                 file_or_pattern, follow_links, follow_mounts, recurse_undeleted_subdirs,
                 recurse_deleted_subdirs, n_days)
    rv = []
    # In AFS, without tokens, this is very slow.  "Don't do that."
    # The old code called readdir() and lstat'd everything before following.
    # The old code also re-implemented glob() with BREs, and we're not doing that.
    file_list = glob.glob(file_or_pattern) + glob.glob('.#' + file_or_pattern)
    if len(file_list) == 0:
        raise DeleteError("{0}: {1}".format(file_or_pattern,
                                            "No match" if has_wildcards(file_or_pattern) else os.strerror(errno.ENOENT)))

    for filename in file_list:
        logger.debug("Examining %s", filename)
        if os.path.isdir(filename):
            logger.debug("%s is a directory", filename)
            if os.path.islink(filename) and not follow_links:
                logger.debug("Skipping symlink: %s", filename)
                continue
            if is_mountpoint(filename) and not follow_mounts:
                logger.debug("Skipping mountpoint: %s", filename)
                continue
            if ((is_deleted(filename) and (recurse_deleted_subdirs != False)) or \
                    (not is_deleted(filename) and (recurse_undeleted_subdirs != False))):
                # NOTE: recurse_undeleted_subdirs is being abused as a tristate with 'None'
                #       meaning "do it on the first time only.
                logger.debug("Recursing into %sdeleted directory: %s",
                             "un" if not is_deleted(filename) else "",
                             filename)
                try:
                    for item in dir_listing(filename):
                        # Escape metachars before recursing because filenames
                        # can in fact contain metacharacters.
                        rv += find_deleted_files(escape_meta(item), follow_links, follow_mounts,
                                                 False if recurse_undeleted_subdirs is None else recurse_undeleted_subdirs,
                                                 False if recurse_deleted_subdirs is None else recurse_deleted_subdirs,
                                                 n_days)
                except OSError as e:
                    perror('{filename}: {error}', filename=e.filename,
                           error=e.strerror)
        if is_deleted(filename):
            try:
                if not n_days_old(filename, n_days):
                    logger.debug("%s is not %d days old, skipping",
                                 filename, n_days)
                    continue
            except OSError as e:
                perror('{filename}: {error} while checking age',
                       filename=e.filename, error=e.strerror)
            logger.debug("Adding: %s", filename)
            rv.append(filename)

    return rv
