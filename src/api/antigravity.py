"""Provider transport is intentionally absent from this archive."""
def __getattr__(name):
    raise NotImplementedError("No provider transport is included in the archival snapshot")
