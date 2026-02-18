"""Volume operations for ModalInterpreter.

This module provides the VolumeOpsMixin class, extracted from ModalInterpreter
for better maintainability and separation of concerns.

The mixin provides volume persistence operations:
    - upload_to_volume: Upload local directories/files to Modal Volume
    - commit: Commit volume changes to persistent storage
    - reload: Reload volume to see changes from other containers
    - _resolve_volume: Internal method to get/create Volume handle

Classes:
    - VolumeOpsMixin: Mixin providing volume persistence capabilities
"""

from __future__ import annotations

import modal


class VolumeOpsMixin:
    """Mixin providing volume persistence operations for ModalInterpreter.

    This mixin handles file persistence via Modal Volumes, allowing sandboxed
    code to store data that survives across sessions.

    Attributes Required on Host Class:
        volume_name: Optional Modal Volume name for persistent storage.
        volume_mount_path: Mount path for volume inside sandbox.
        _volume: The Modal Volume handle (set after start()).

    Methods:
        upload_to_volume: Upload local directories/files to the volume.
        commit: Commit volume changes to persistent storage.
        reload: Reload volume to see changes from other containers.
        _resolve_volume: Internal method to get/create Volume handle.
    """

    # These attributes are provided by the host class
    volume_name: str | None
    volume_mount_path: str
    _volume: modal.Volume | None

    def _resolve_volume(self) -> modal.Volume:
        """Return a Volume V2 handle (created lazily if needed).

        Returns:
            A Modal Volume V2 handle, creating the volume if it doesn't exist.

        Raises:
            ValueError: If volume_name was not configured.
        """
        if self.volume_name is None:
            raise ValueError("volume_name was not configured")

        return modal.Volume.from_name(
            self.volume_name, create_if_missing=True, version=2
        )

    def commit(self) -> None:
        """Commit volume changes to persistent storage.

        Only works if a volume was specified at init. No-op otherwise.
        """
        if self._volume is not None:
            self._volume.commit()

    def reload(self) -> None:
        """Reload volume to see changes from other containers.

        Only works if a volume was specified at init. No-op otherwise.
        """
        if self._volume is not None:
            self._volume.reload()

    def upload_to_volume(
        self,
        local_dirs: dict[str, str] | None = None,
        local_files: dict[str, str] | None = None,
    ) -> None:
        """Upload local directories/files to the Modal Volume if they don't exist.

        Args:
            local_dirs: Mapping of local directory path -> remote directory
                path on the volume. E.g. {"rlm_content/dspy-knowledge": "/dspy-knowledge"}
            local_files: Mapping of local file path -> remote file path.
        """
        if not self.volume_name:
            raise ValueError("No volume_name configured on this interpreter.")

        vol = self._resolve_volume()

        def _exists(remote_path: str) -> bool:
            """Check if a remote file or directory exists."""
            remote_path = remote_path.rstrip("/")
            if not remote_path:  # Root always exists
                return True

            parent = "/"
            if "/" in remote_path:
                parent, name = remote_path.rsplit("/", 1)
                parent = parent or "/"
            else:
                name = remote_path

            try:
                # listdir returns FileEntry objects with a .path attribute (filename)
                for entry in vol.listdir(parent):
                    if entry.path == name:
                        return True
            except Exception:
                # Parent probably doesn't exist
                pass
            return False

        # Use force=True to handle cases where we proceed with upload,
        # ensuring no spurious FileExistsErrors from the batch mechanism itself.
        with vol.batch_upload(force=True) as batch:
            for local_dir, remote_dir in (local_dirs or {}).items():
                if _exists(remote_dir):
                    print(f"Volume: '{remote_dir}' exists, skipping upload.")
                    continue
                print(f"Volume: Uploading directory '{local_dir}' to '{remote_dir}'...")
                batch.put_directory(local_dir, remote_dir)

            for local_file, remote_file in (local_files or {}).items():
                if _exists(remote_file):
                    print(f"Volume: '{remote_file}' exists, skipping upload.")
                    continue
                print(f"Volume: Uploading file '{local_file}' to '{remote_file}'...")
                batch.put_file(local_file, remote_file)
