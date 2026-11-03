"""Describe the raw structure of one page, for `storage layout`."""

from .database import Table
from .heap import NO_PAGE, DirectoryPage
from .page import HEADER, SLOT, SlottedPage


def describe_page(table: Table, page_id: int) -> dict:
    heap = table.heap
    directory_ids = heap.directory_ids()
    frame = heap.pool.fetch_page(heap.disk, page_id)
    try:
        if page_id in directory_ids:
            return describe_directory(page_id, DirectoryPage(frame.data))
        return describe_data(SlottedPage(frame.data))
    finally:
        heap.pool.unpin_page(heap.disk, page_id, False)


def describe_directory(page_id: int, page: DirectoryPage) -> dict:
    next_page = page.next_page
    return {'page_id': page_id, 'kind': 'directory',
            'next_dir_page': None if next_page == NO_PAGE else next_page,
            'entry_count': page.entry_count,
            'entries': [{'offset': 10 + 6 * i, 'data_page_id': data_id, 'free_bytes': free}
                        for i, (data_id, free) in enumerate(page.entries())]}


def describe_data(page: SlottedPage) -> dict:
    slots = []
    for slot_no in range(page.slot_count):
        offset, length = page.slot(slot_no)
        slots.append({'slot': slot_no, 'slot_offset': HEADER.size + SLOT.size * slot_no,
                      'offset': offset, 'length': length, 'live': offset != 0})
    return {'page_id': page.page_id, 'kind': 'data', 'slot_count': page.slot_count,
            'free_end': page.free_end, 'free_space': page.free_space(), 'slots': slots}


def format_layout(table_name: str, layout: dict) -> str:
    lines = [f"{table_name}.db page {layout['page_id']} ({layout['kind']} page)"]
    if layout['kind'] == 'directory':
        lines.append(f"  bytes 0-3   page_id        {layout['page_id']}")
        lines.append(f"  bytes 4-7   next_dir_page  {layout['next_dir_page'] if layout['next_dir_page'] is not None else 'none (0xFFFFFFFF)'}")
        lines.append(f"  bytes 8-9   entry_count    {layout['entry_count']}")
        lines.append('  entry  offset  data_page_id  free_bytes')
        for index, entry in enumerate(layout['entries']):
            lines.append(f"  {index:<5}  {entry['offset']:<6}  {entry['data_page_id']:<12}  {entry['free_bytes']}")
        return '\n'.join(lines)
    lines.append(f"  bytes 0-3   page_id     {layout['page_id']}")
    lines.append(f"  bytes 4-5   slot_count  {layout['slot_count']}")
    lines.append(f"  bytes 6-7   free_end    {layout['free_end']}")
    lines.append(f"  free space  {layout['free_space']} bytes, from byte {8 + 4 * layout['slot_count']} to {layout['free_end'] - 1}")
    lines.append('  slot  at byte  offset  length  status')
    for slot in layout['slots']:
        status = 'live' if slot['live'] else 'dead'
        lines.append(f"  {slot['slot']:<4}  {slot['slot_offset']:<7}  {slot['offset']:<6}  {slot['length']:<6}  {status}")
    return '\n'.join(lines)

