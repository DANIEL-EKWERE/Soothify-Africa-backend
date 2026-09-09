"""Compile .po catalogs to .mo without the GNU gettext toolchain.

Django's built-in `compilemessages` shells out to `msgfmt`, which is not installed
on this machine. This command writes the binary .mo format directly, so the normal
translation workflow works. If you later `apt install gettext`, the standard
`manage.py compilemessages` will work too and this command becomes optional.
"""

import array
import re
import struct
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

# "msgid"/"msgstr" plus any number of continuation string lines.
_ENTRY = re.compile(
    r'^msgid\s+(?P<id>"(?:[^"\\]|\\.)*"(?:\s*\n\s*"(?:[^"\\]|\\.)*")*)\s*\n'
    r'^msgstr\s+(?P<str>"(?:[^"\\]|\\.)*"(?:\s*\n\s*"(?:[^"\\]|\\.)*")*)',
    re.M,
)
_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\'}


def _unquote(blob):
    """Join adjacent quoted chunks and unescape them."""
    out = []
    for chunk in re.findall(r'"((?:[^"\\]|\\.)*)"', blob):
        i = 0
        while i < len(chunk):
            c = chunk[i]
            if c == '\\' and i + 1 < len(chunk):
                out.append(_ESCAPES.get(chunk[i + 1], chunk[i + 1]))
                i += 2
            else:
                out.append(c)
                i += 1
    return ''.join(out)


def parse_po(text):
    catalog = {}
    for m in _ENTRY.finditer(text):
        msgid, msgstr = _unquote(m.group('id')), _unquote(m.group('str'))
        # An empty msgstr means untranslated; the "" msgid holds the header.
        if msgstr or msgid == '':
            catalog[msgid] = msgstr
    return catalog


def write_mo(catalog, path):
    """Serialise a catalog in the GNU .mo binary format."""
    keys = sorted(catalog)
    ids = b''
    strs = b''
    offsets = []
    for k in keys:
        kb, vb = k.encode('utf-8'), catalog[k].encode('utf-8')
        offsets.append((len(ids), len(kb), len(strs), len(vb)))
        ids += kb + b'\x00'
        strs += vb + b'\x00'

    n = len(keys)
    keystart = 7 * 4 + 16 * n
    valuestart = keystart + len(ids)
    koffsets, voffsets = [], []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]

    output = struct.pack(
        'Iiiiiii',
        0x950412DE,          # magic
        0,                   # version
        n,                   # number of entries
        7 * 4,               # start of key index
        7 * 4 + n * 8,       # start of value index
        0, 0,                # hash table size / offset (unused)
    )
    output += array.array('i', koffsets + voffsets).tobytes()
    output += ids + strs
    path.write_bytes(output)
    return n


class Command(BaseCommand):
    help = 'Compile locale/*/LC_MESSAGES/*.po to .mo in pure Python.'

    def handle(self, *args, **options):
        roots = [Path(p) for p in settings.LOCALE_PATHS]
        found = 0
        for root in roots:
            for po in sorted(root.glob('*/LC_MESSAGES/*.po')):
                text = po.read_text(encoding='utf-8')
                catalog = parse_po(text)
                mo = po.with_suffix('.mo')
                n = write_mo(catalog, mo)
                # every msgid in the file, including the untranslated ones that
                # parse_po drops (an empty msgstr must fall back to the source string)
                total = text.count('\nmsgid ') + text.startswith('msgid ')
                total = max(total - 1, 0)          # minus the header entry
                translated = sum(1 for k, v in catalog.items() if k and v)
                self.stdout.write(self.style.SUCCESS(
                    f'{po.relative_to(root.parent)} -> {mo.name}: '
                    f'{translated}/{total} translated ({n} entries written)'
                ))
                found += 1
        if not found:
            self.stdout.write(self.style.WARNING('No .po files found.'))
