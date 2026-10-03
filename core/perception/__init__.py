"""Lazy adapters for MO's bounded PDF perception path.

Local images reuse the provider image transport and live screen requests reuse
``computer_observe``.  This package therefore owns only the optional embedded-
text PDF adapter; importing it does not load ``pypdf``.
"""
