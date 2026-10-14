from html import escape


def escape_html(value: object) -> str:
    """Safely render dynamic text in Telegram HTML parse mode."""
    return escape(str(value if value is not None else ""), quote=False)
