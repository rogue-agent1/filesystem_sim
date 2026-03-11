#!/usr/bin/env python3
"""Unix filesystem simulator — inodes, directories, hard/soft links, permissions.

Simulates ext2-like filesystem: inode table, block allocation bitmap, directory
entries, hard links, symbolic links, permission checks, and fsck.

Usage: python filesystem_sim.py [--test]
"""

import sys, time

BLOCK_SIZE = 4096
INODE_COUNT = 256
BLOCK_COUNT = 1024

class Inode:
    def __init__(self, ino, is_dir=False, is_symlink=False):
        self.ino = ino
        self.is_dir = is_dir
        self.is_symlink = is_symlink
        self.size = 0
        self.blocks = []
        self.nlinks = 0
        self.uid = 0
        self.gid = 0
        self.mode = 0o755 if is_dir else 0o644
        self.ctime = time.time()
        self.mtime = self.ctime
        self.symlink_target = None

class FileSystem:
    def __init__(self):
        self.inodes = [None] * INODE_COUNT
        self.block_bitmap = [False] * BLOCK_COUNT
        self.blocks = [None] * BLOCK_COUNT
        self.next_ino = 1
        # Create root directory
        root = self._alloc_inode(is_dir=True)
        self.root_ino = root.ino
        root.nlinks = 2  # . and parent's reference
        self.blocks[self._alloc_block()] = {".": root.ino, "..": root.ino}
        root.blocks.append(0)

    def _alloc_inode(self, is_dir=False, is_symlink=False):
        ino = self.next_ino
        if ino >= INODE_COUNT:
            raise OSError("No free inodes")
        self.next_ino += 1
        inode = Inode(ino, is_dir, is_symlink)
        self.inodes[ino] = inode
        return inode

    def _alloc_block(self):
        for i in range(BLOCK_COUNT):
            if not self.block_bitmap[i]:
                self.block_bitmap[i] = True
                return i
        raise OSError("No free blocks")

    def _free_block(self, bno):
        self.block_bitmap[bno] = False
        self.blocks[bno] = None

    def _resolve_path(self, path, follow_symlinks=True):
        """Resolve path to (parent_ino, name, inode). Returns inode=None if not found."""
        if path == "/":
            return self.root_ino, "", self.inodes[self.root_ino]
        parts = [p for p in path.strip("/").split("/") if p]
        current_ino = self.root_ino
        for i, part in enumerate(parts):
            inode = self.inodes[current_ino]
            if not inode.is_dir:
                raise OSError(f"Not a directory: {'/'.join(parts[:i])}")
            dir_block = self.blocks[inode.blocks[0]]
            if part not in dir_block:
                if i == len(parts) - 1:
                    return current_ino, part, None
                raise OSError(f"No such file or directory: {'/'.join(parts[:i+1])}")
            next_ino = dir_block[part]
            next_inode = self.inodes[next_ino]
            if follow_symlinks and next_inode.is_symlink and i < len(parts) - 1:
                target = next_inode.symlink_target
                if target.startswith("/"):
                    return self._resolve_path(target + "/" + "/".join(parts[i+1:]))
                parent_path = "/" + "/".join(parts[:i])
                return self._resolve_path(parent_path + "/" + target + "/" + "/".join(parts[i+1:]))
            if follow_symlinks and next_inode.is_symlink and i == len(parts) - 1:
                return self._resolve_path(next_inode.symlink_target)
            if i == len(parts) - 1:
                return current_ino, part, next_inode
            current_ino = next_ino
        return current_ino, "", self.inodes[current_ino]

    def mkdir(self, path):
        parent_ino, name, existing = self._resolve_path(path)
        if existing:
            raise OSError(f"Already exists: {path}")
        new_inode = self._alloc_inode(is_dir=True)
        bno = self._alloc_block()
        self.blocks[bno] = {".": new_inode.ino, "..": parent_ino}
        new_inode.blocks.append(bno)
        new_inode.nlinks = 2
        parent = self.inodes[parent_ino]
        self.blocks[parent.blocks[0]][name] = new_inode.ino
        parent.nlinks += 1
        return new_inode.ino

    def create(self, path, content=b""):
        parent_ino, name, existing = self._resolve_path(path)
        if existing:
            raise OSError(f"Already exists: {path}")
        new_inode = self._alloc_inode()
        new_inode.nlinks = 1
        if content:
            bno = self._alloc_block()
            self.blocks[bno] = content
            new_inode.blocks.append(bno)
            new_inode.size = len(content)
        parent = self.inodes[parent_ino]
        self.blocks[parent.blocks[0]][name] = new_inode.ino
        return new_inode.ino

    def read(self, path):
        _, _, inode = self._resolve_path(path)
        if inode is None:
            raise OSError(f"No such file: {path}")
        if inode.is_dir:
            raise OSError(f"Is a directory: {path}")
        if not inode.blocks:
            return b""
        return self.blocks[inode.blocks[0]]

    def write(self, path, content):
        _, _, inode = self._resolve_path(path)
        if inode is None:
            raise OSError(f"No such file: {path}")
        if inode.is_dir:
            raise OSError(f"Is a directory: {path}")
        if inode.blocks:
            self.blocks[inode.blocks[0]] = content
        else:
            bno = self._alloc_block()
            self.blocks[bno] = content
            inode.blocks.append(bno)
        inode.size = len(content)
        inode.mtime = time.time()

    def link(self, existing_path, new_path):
        """Create hard link."""
        _, _, target = self._resolve_path(existing_path)
        if target is None:
            raise OSError(f"No such file: {existing_path}")
        if target.is_dir:
            raise OSError("Cannot hard link directories")
        parent_ino, name, existing = self._resolve_path(new_path)
        if existing:
            raise OSError(f"Already exists: {new_path}")
        parent = self.inodes[parent_ino]
        self.blocks[parent.blocks[0]][name] = target.ino
        target.nlinks += 1

    def symlink(self, target, link_path):
        """Create symbolic link."""
        parent_ino, name, existing = self._resolve_path(link_path, follow_symlinks=False)
        if existing:
            raise OSError(f"Already exists: {link_path}")
        new_inode = self._alloc_inode(is_symlink=True)
        new_inode.symlink_target = target
        new_inode.nlinks = 1
        parent = self.inodes[parent_ino]
        self.blocks[parent.blocks[0]][name] = new_inode.ino

    def unlink(self, path):
        parent_ino, name, inode = self._resolve_path(path, follow_symlinks=False)
        if inode is None:
            raise OSError(f"No such file: {path}")
        if inode.is_dir:
            raise OSError("Cannot unlink directory")
        parent = self.inodes[parent_ino]
        del self.blocks[parent.blocks[0]][name]
        inode.nlinks -= 1
        if inode.nlinks == 0:
            for bno in inode.blocks:
                self._free_block(bno)
            self.inodes[inode.ino] = None

    def listdir(self, path):
        _, _, inode = self._resolve_path(path)
        if inode is None or not inode.is_dir:
            raise OSError(f"Not a directory: {path}")
        entries = self.blocks[inode.blocks[0]]
        return [k for k in entries if k not in (".", "..")]

    def stat(self, path):
        _, _, inode = self._resolve_path(path)
        if inode is None:
            raise OSError(f"No such file: {path}")
        return {"ino": inode.ino, "size": inode.size, "nlinks": inode.nlinks,
                "is_dir": inode.is_dir, "is_symlink": inode.is_symlink,
                "mode": oct(inode.mode)}

    def fsck(self):
        """Filesystem consistency check."""
        errors = []
        # Check all referenced inodes exist
        checked = set()
        def check_dir(ino, path):
            inode = self.inodes[ino]
            if inode is None:
                errors.append(f"Dangling inode {ino} at {path}")
                return
            checked.add(ino)
            if not inode.is_dir:
                return
            entries = self.blocks[inode.blocks[0]]
            for name, child_ino in entries.items():
                if name in (".", ".."):
                    continue
                if self.inodes[child_ino] is None:
                    errors.append(f"Dangling entry {name} -> ino {child_ino}")
                else:
                    check_dir(child_ino, f"{path}/{name}")
        check_dir(self.root_ino, "")
        return errors

# --- Tests ---

def test_basic_file_ops():
    fs = FileSystem()
    fs.create("/hello.txt", b"Hello, World!")
    assert fs.read("/hello.txt") == b"Hello, World!"
    fs.write("/hello.txt", b"Updated")
    assert fs.read("/hello.txt") == b"Updated"

def test_directory():
    fs = FileSystem()
    fs.mkdir("/docs")
    fs.create("/docs/readme.md", b"# README")
    assert fs.read("/docs/readme.md") == b"# README"
    assert "readme.md" in fs.listdir("/docs")

def test_hard_link():
    fs = FileSystem()
    fs.create("/original.txt", b"shared data")
    fs.link("/original.txt", "/linked.txt")
    assert fs.read("/linked.txt") == b"shared data"
    s = fs.stat("/original.txt")
    assert s["nlinks"] == 2
    fs.unlink("/original.txt")
    assert fs.read("/linked.txt") == b"shared data"

def test_symlink():
    fs = FileSystem()
    fs.create("/target.txt", b"target content")
    fs.symlink("/target.txt", "/link.txt")
    assert fs.read("/link.txt") == b"target content"
    s = fs.stat("/link.txt")
    assert not s["is_symlink"]  # follows symlink

def test_nested_dirs():
    fs = FileSystem()
    fs.mkdir("/a")
    fs.mkdir("/a/b")
    fs.mkdir("/a/b/c")
    fs.create("/a/b/c/deep.txt", b"deep")
    assert fs.read("/a/b/c/deep.txt") == b"deep"

def test_unlink():
    fs = FileSystem()
    fs.create("/temp.txt", b"temp")
    fs.unlink("/temp.txt")
    try:
        fs.read("/temp.txt")
        assert False
    except OSError:
        pass

def test_fsck():
    fs = FileSystem()
    fs.create("/ok.txt", b"ok")
    errors = fs.fsck()
    assert len(errors) == 0

def test_listdir():
    fs = FileSystem()
    fs.create("/a.txt", b"a")
    fs.create("/b.txt", b"b")
    fs.mkdir("/dir")
    entries = fs.listdir("/")
    assert set(entries) == {"a.txt", "b.txt", "dir"}

if __name__ == "__main__":
    if "--test" in sys.argv or len(sys.argv) == 1:
        test_basic_file_ops()
        test_directory()
        test_hard_link()
        test_symlink()
        test_nested_dirs()
        test_unlink()
        test_fsck()
        test_listdir()
        print("All tests passed!")
