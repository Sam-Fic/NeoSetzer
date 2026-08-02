#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import os


def read_text_with_encoding(filepath):
    """Read a text file with automatic encoding detection.

    Reads the file as binary first, then detects encoding using BOM check,
    UTF-8 strict try, chardet (if available), and a smarter fallback
    with locale-prioritized ordering.

    Returns (text, encoding, has_bom) tuple where encoding is the detected
    encoding and has_bom indicates if the original file had a BOM.
    """
    with open(filepath, 'rb') as f:
        raw_bytes = f.read()

    encoding, has_bom = detect_encoding(raw_bytes)

    # Strip BOM from raw bytes if present, then decode
    # This ensures BOM is never in the text content
    raw_bytes_without_bom = _strip_bom_bytes(raw_bytes)

    try:
        text = raw_bytes_without_bom.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        encoding = 'utf-8'
        has_bom = False
        text = raw_bytes_without_bom.decode(encoding, errors='replace')

    return text, encoding, has_bom


def write_text_with_encoding(filepath, text, encoding='utf-8', has_bom=False):
    """Write text to a file using the specified encoding.

    If has_bom is True, prepends the appropriate BOM for the encoding.
    If the encoding fails, falls back to UTF-8 without BOM.
    """
    try:
        encoded = text.encode(encoding)
        if has_bom:
            encoded = _prepend_bom(encoded, encoding)
        with open(filepath, 'wb') as f:
            f.write(encoded)
    except (UnicodeEncodeError, LookupError):
        with open(filepath, 'wb') as f:
            f.write(text.encode('utf-8', errors='replace'))


def _strip_bom_bytes(raw_bytes):
    """Remove BOM bytes from the beginning of raw bytes, if present."""
    if raw_bytes.startswith(b'\xef\xbb\xbf'):
        return raw_bytes[3:]
    if raw_bytes.startswith(b'\xff\xfe'):
        return raw_bytes[2:]
    if raw_bytes.startswith(b'\xfe\xff'):
        return raw_bytes[2:]
    return raw_bytes


def _prepend_bom(encoded_bytes, encoding):
    """Prepend appropriate BOM bytes for the given encoding."""
    if encoding.lower() in ('utf-8', 'utf8'):
        return b'\xef\xbb\xbf' + encoded_bytes
    if encoding.lower() in ('utf-16-le', 'utf16_le', 'utf-16le'):
        return b'\xff\xfe' + encoded_bytes
    if encoding.lower() in ('utf-16-be', 'utf16_be', 'utf-16be'):
        return b'\xfe\xff' + encoded_bytes
    return encoded_bytes


def detect_encoding(raw_bytes):
    """Detect encoding of raw bytes using multiple strategies.

    Strategy order:
    1. BOM check (UTF-8, UTF-16 LE/BE) - reliable indicator
    2. Escape sequence check (ISO-2022-* encodings) - Japanese/Korean email
    3. UTF-8 strict try (most common for modern files)
    4. chardet if available with confidence > 0.6
    5. Language-script scoring: decode with CJK encodings, score by
       character counts in each script range
    6. Locale-prioritized fallback (for edge cases with short texts)
    7. Final fallback: 'utf-8'

    Returns (encoding, has_bom) tuple. The encoding name does NOT include
    the BOM suffix (e.g., 'utf-8' not 'utf-8-sig'); has_bom indicates whether
    the original file had a BOM.
    """
    # 1. BOM check
    bom_info = _check_bom(raw_bytes)
    if bom_info is not None:
        encoding, has_bom = bom_info
        return encoding, has_bom

    # 2. Escape sequence check (ISO-2022 encodings are ASCII-compatible,
    # so they pass the UTF-8 try and need to be detected separately)
    iso_encoding = _check_iso2022(raw_bytes)
    if iso_encoding is not None:
        return iso_encoding, False

    # 3. UTF-8 strict try (most common)
    try:
        raw_bytes.decode('utf-8')
        return 'utf-8', False
    except UnicodeDecodeError:
        pass

    # 4. chardet with higher confidence threshold
    chardet_result = _try_chardet(raw_bytes)
    if chardet_result is not None:
        return chardet_result, False

    # 5. Language-script scoring (handles most CJK cases even without chardet)
    script_encoding = _detect_by_script(raw_bytes)
    if script_encoding is not None:
        return script_encoding, False

    # 6. Locale-prioritized fallback (edge case for very short ambiguous texts)
    locale_encoding = _locale_fallback(raw_bytes)
    if locale_encoding is not None:
        return locale_encoding, False

    return 'utf-8', False


def _check_bom(raw_bytes):
    """Check for Byte Order Mark. Returns (encoding, has_bom) or None."""
    if raw_bytes.startswith(b'\xef\xbb\xbf'):
        return 'utf-8', True
    if raw_bytes.startswith(b'\xff\xfe'):
        return 'utf-16-le', True
    if raw_bytes.startswith(b'\xfe\xff'):
        return 'utf-16-be', True
    return None


def _check_iso2022(raw_bytes):
    """Check for ISO-2022 escape sequences. Returns encoding or None.

    ISO-2022 encodings (ISO-2022-JP, ISO-2022-KR) use escape sequences to
    switch between character sets. These are ASCII-compatible so they
    pass the UTF-8 try but produce garbage when decoded as UTF-8.
    """
    # Check for ISO-2022-JP escape sequences:
    # ESC $ B  - JIS X 0208-1983 (Japanese kanji/kana)
    # ESC $ @  - JIS X 0208-1978 (older kanji)
    # ESC ( B  - ASCII mode
    # ESC ( J  - JIS-Roman
    if b'\x1b$B' in raw_bytes or b'\x1b$@' in raw_bytes:
        # Verify it can be decoded as ISO-2022-JP
        try:
            text = raw_bytes.decode('iso2022_jp')
            # Count non-ASCII characters; if there are many, it's likely correct
            non_ascii = sum(1 for c in text if ord(c) > 127)
            if non_ascii > 5:
                return 'iso2022_jp'
        except (UnicodeDecodeError, LookupError):
            pass

    # Check for ISO-2022-KR escape sequences:
    # ESC $ ) C  - KS C 5601-1987 (Korean hangul/hanja)
    if b'\x1b$)C' in raw_bytes:
        try:
            text = raw_bytes.decode('iso2022_kr')
            non_ascii = sum(1 for c in text if ord(c) > 127)
            if non_ascii > 5:
                return 'iso2022_kr'
        except (UnicodeDecodeError, LookupError):
            pass

    return None


def _count_script_chars(text):
    """Count characters in each script range.

    Returns dict with counts for: cjk, hiragana, katakana, hangul, ascii, other
    """
    counts = {
        'cjk': 0,
        'hiragana': 0,
        'katakana': 0,
        'hangul': 0,
        'ascii': 0,
        'other': 0,
    }

    for char in text:
        cp = ord(char)
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            counts['cjk'] += 1
        elif 0x3040 <= cp <= 0x309F:
            counts['hiragana'] += 1
        elif 0x30A0 <= cp <= 0x30FF:
            counts['katakana'] += 1
        elif 0xAC00 <= cp <= 0xD7AF:
            counts['hangul'] += 1
        elif cp < 0x80:
            counts['ascii'] += 1
        else:
            counts['other'] += 1

    return counts


def _detect_by_script(raw_bytes):
    """Try CJK encodings and pick the one whose decoded characters best match
    the expected script distribution.

    Strategy: For each encoding, determine which scripts it produces.
    Prefer encodings that produce characters from FEWER scripts (more
    concentrated = more likely correct). Break ties by total count of
    characters in the primary script.
    """
    encodings_to_try = [
        'gb18030', 'gbk', 'gb2312', 'big5',
        'shift_jis', 'euc_jp', 'iso2022_jp', 'cp932',
        'euc-kr', 'cp949',
    ]

    # Decode with all encodings and analyze script distribution
    results = {}
    for enc in encodings_to_try:
        try:
            text = raw_bytes.decode(enc)
            counts = _count_script_chars(text)
            # Determine which scripts are present (non-zero count)
            scripts_present = set()
            if counts['cjk'] > 0:
                scripts_present.add('cjk')
            if counts['hiragana'] > 0 or counts['katakana'] > 0:
                scripts_present.add('kana')
            if counts['hangul'] > 0:
                scripts_present.add('hangul')
            results[enc] = {
                'text': text,
                'counts': counts,
                'scripts': scripts_present,
                'num_scripts': len(scripts_present),
            }
        except (UnicodeDecodeError, LookupError):
            pass

    if not results:
        return None

    # Group encodings by script
    chinese_encs = ['gb18030', 'gbk', 'gb2312', 'big5']
    japanese_encs = ['shift_jis', 'euc_jp', 'iso2022_jp', 'cp932']
    korean_encs = ['euc-kr', 'cp949']

    # Find the best encoding
    # Strategy: Prefer encodings that produce FEWER scripts (purity).
    # Highly specific scripts (Kana, Hangul) get large bonuses because
    # only their respective encodings can produce them.
    best_encoding = None
    best_score = -999

    for enc, info in results.items():
        # Determine the expected script for this encoding
        if enc in chinese_encs:
            primary_script = 'cjk'
        elif enc in japanese_encs:
            primary_script = 'kana'
        else:
            primary_script = 'hangul'

        # Calculate base score: fewer scripts = higher score
        score = (10 - info['num_scripts']) * 100

        # Primary script count bonus (weighted by specificity)
        if primary_script == 'kana':
            primary_count = info['counts']['hiragana'] + info['counts']['katakana']
            score += primary_count * 20  # Kana is very specific
        elif primary_script == 'hangul':
            primary_count = info['counts']['hangul']
            score += primary_count * 20  # Hangul is very specific
        else:  # cjk
            primary_count = info['counts']['cjk']
            score += primary_count * 5   # CJK is less specific

        # Bonus: if the encoding produces ONLY its expected script (pure)
        if info['num_scripts'] == 1 and primary_script in info['scripts']:
            score += 200
            # Extra bonus for pure Kana/Hangul (more specific)
            if primary_script in ('kana', 'hangul'):
                score += 200

        # Penalty: if the encoding produces unexpected scripts
        if primary_script == 'cjk' and ('kana' in info['scripts'] or 'hangul' in info['scripts']):
            score -= 100
        if primary_script == 'kana' and ('hangul' in info['scripts']):
            score -= 100
        if primary_script == 'hangul' and ('kana' in info['scripts']):
            score -= 100

        if score > best_score:
            best_score = score
            best_encoding = enc

    return best_encoding


def _locale_fallback(raw_bytes):
    """Fallback based on system locale. Only used for short/ambiguous texts."""
    locale = os.environ.get('LANG', '').lower()

    # Ordered list of encodings to try, prioritized by locale
    if 'zh_cn' in locale:
        encodings = ['gb18030', 'gbk']
    elif 'zh_tw' in locale:
        encodings = ['big5', 'gb18030']
    elif 'ja_jp' in locale:
        encodings = ['shift_jis', 'euc_jp']
    elif 'ko_kr' in locale:
        encodings = ['euc-kr', 'cp949']
    else:
        # No locale hint - try common encodings
        encodings = ['gb18030', 'shift_jis', 'euc-kr']

    for enc in encodings:
        try:
            raw_bytes.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue

    return None


def _try_chardet(raw_bytes):
    """Try to detect encoding using chardet library. Returns encoding or None."""
    try:
        import chardet
    except ImportError:
        return None

    try:
        result = chardet.detect(raw_bytes)
        encoding = result.get('encoding')
        confidence = result.get('confidence', 0)
        if encoding is not None and confidence > 0.6:
            # Normalize encoding name
            encoding_lower = encoding.lower().replace('-', '_')
            if encoding_lower == 'ascii':
                return 'utf-8'
            # Verify chardet's result actually decodes successfully
            try:
                raw_bytes.decode(encoding)
                return encoding
            except (UnicodeDecodeError, LookupError):
                return None
    except Exception:
        pass

    return None
