"""Agent-domain exceptions shared without exposing transport modules to graph code."""


class HumanLockedError(Exception):
    """The incident was locked by an analyst between read and write."""


__all__ = ["HumanLockedError"]
