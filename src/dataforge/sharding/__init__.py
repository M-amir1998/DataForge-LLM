"""Size-bounded, model-ready shard writing (Parquet or zstd JSONL)."""

from dataforge.sharding.writer import ShardInfo, ShardWriter, SplitShardWriter

__all__ = ["ShardInfo", "ShardWriter", "SplitShardWriter"]
