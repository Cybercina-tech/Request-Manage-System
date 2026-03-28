"""Text encoding helpers (mojibake / mis-decoded UTF-8)."""


def repair_utf8_misread_as_latin1(s: str | None) -> str:
    """
    Recover text when UTF-8 bytes were wrongly decoded as Latin-1 (classic mojibake: Ù†Ø¸…).

    Correct Persian/Arabic Unicode cannot be encoded as Latin-1, so it is returned unchanged.
    Some tools map UTF-8 continuation bytes to †/‡ (U+2020/U+2021); those are normalized first.
    """
    if s is None:
        return ''
    if not isinstance(s, str):
        s = str(s)
    if not s:
        return ''
    for candidate in (s, s.replace('\u2020', '\x86').replace('\u2021', '\x87')):
        try:
            return candidate.encode('latin-1').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return s
