"""Backward-compatibility re-export shim.

.. deprecated::
    Import from :mod:`sirius_chat.mixins` instead::

        from sirius_chat.mixins import JsonSerializable
"""
# ruff: noqa: F401
from sirius_chat.mixins import JsonSerializable  # noqa: F401

__all__ = ["JsonSerializable"]
