#!/usr/bin/env python3
"""Pack an Ndless ELF into a Zehn file and optionally wrap it in a loader .tns."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from elftools.elf.sections import SymbolTableSection


ZEHN_SIGNATURE = 0x6E68655A
ZEHN_VERSION = 1

R_ARM_ABS32 = 2
R_ARM_REL32 = 3
R_ARM_BASE_PREL = 25
R_ARM_GOT_BREL = 26
R_ARM_TARGET1 = 38

RELOC_ADD_BASE = 0
RELOC_ADD_BASE_GOT = 1
RELOC_SET_ZERO = 2
RELOC_FILE_COMPRESSED = 3
RELOC_UNALIGNED = 4

FLAG_NDLESS_VERSION_MIN = 0
FLAG_NDLESS_VERSION_MAX = 1
FLAG_NDLESS_REVISION_MIN = 2
FLAG_NDLESS_REVISION_MAX = 3
FLAG_RUNS_ON_COLOR = 4
FLAG_RUNS_ON_CLICKPAD = 5
FLAG_RUNS_ON_TOUCHPAD = 6
FLAG_RUNS_ON_32MB = 7
FLAG_EXECUTABLE_NAME = 8
FLAG_EXECUTABLE_AUTHOR = 9
FLAG_EXECUTABLE_VERSION = 10
FLAG_EXECUTABLE_NOTICE = 11
FLAG_RUNS_ON_HWW = 12
FLAG_USES_LCD_BLIT = 13

HEADER_STRUCT = struct.Struct("<8I")


class ZehnPackError(RuntimeError):
    """Raised when the ELF cannot be converted into a usable Zehn binary."""


def pack_u24_record(kind: int, value: int) -> bytes:
    if not 0 <= value < (1 << 24):
        raise ZehnPackError(f"value {value} does not fit into a Zehn u24 field")
    return bytes((kind, value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF))


def append_c_string(extra_data: bytearray, value: str) -> int:
    position = len(extra_data)
    extra_data.extend(value.encode("utf-8"))
    extra_data.append(0)
    return position


def load_symtab(elf: ELFFile) -> tuple[SymbolTableSection, set[int], bool, bool]:
    symtab = elf.get_section_by_name(".symtab")
    if not isinstance(symtab, SymbolTableSection):
        raise ZehnPackError("ELF is missing .symtab; cannot build Zehn metadata")

    undefined_symbols: set[int] = set()
    using_old_lcd_api = False
    using_new_lcd_api = False

    for index, symbol in enumerate(symtab.iter_symbols()):
        name = symbol.name
        shndx = symbol.entry["st_shndx"]
        bind = symbol.entry["st_info"]["bind"]
        symbol_type = symbol.entry["st_info"]["type"]

        if name == "_genzehn_new_lcd_api":
            using_new_lcd_api = True
        elif name == "_genzehn_old_lcd_api":
            using_old_lcd_api = True

        if shndx != "SHN_UNDEF":
            continue
        if bind != "STB_WEAK" and symbol_type != "STT_NOTYPE":
            raise ZehnPackError(f"symbol '{name}' is undefined and not weak")
        undefined_symbols.add(index)

    return symtab, undefined_symbols, using_old_lcd_api, using_new_lcd_api


def build_exec_image(elf: ELFFile, include_bss: bool) -> tuple[bytearray, int, int]:
    exec_data = bytearray()
    skipped_nobits = 0
    got_address = 0

    for section in elf.iter_sections():
        if section["sh_type"] == "SHT_SYMTAB":
            continue
        if (section["sh_flags"] & 0x2) == 0:
            continue

        name = section.name
        address = section["sh_addr"]
        size = section["sh_size"]

        if name == ".got":
            got_address = address

        if address < len(exec_data):
            raise ZehnPackError(f"section '{name}' overlaps an earlier section")
        if address > len(exec_data):
            exec_data.extend(b"\0" * (address - len(exec_data)))

        if section["sh_type"] == "SHT_NOBITS":
            if include_bss:
                exec_data.extend(b"\0" * size)
            else:
                skipped_nobits = size
            continue

        if skipped_nobits:
            exec_data.extend(b"\0" * skipped_nobits)
            skipped_nobits = 0

        data = section.data()
        if len(data) != size:
            raise ZehnPackError(f"section '{name}' data size mismatch")
        exec_data.extend(data)

    return exec_data, skipped_nobits, got_address


def build_reloc_table(
    elf: ELFFile,
    symtab: SymbolTableSection,
    undefined_symbols: set[int],
    exec_data: bytearray,
    got_address: int,
) -> bytes:
    relocs: list[bytes] = []
    undo_relocs: set[int] = set()
    saw_unaligned = False

    for section in elf.iter_sections():
        if section.name == ".got":
            data = section.data()
            if len(data) < 4 or struct.unpack_from("<I", data, len(data) - 4)[0] != 0xFFFFFFFF:
                raise ZehnPackError(".got does not end with 0xFFFFFFFF")
            if section["sh_addr"] & 0x3:
                raise ZehnPackError(".got is not 4-byte aligned")
            relocs.append(pack_u24_record(RELOC_ADD_BASE_GOT, section["sh_addr"]))
            continue

        if section["sh_type"] == "SHT_RELA":
            raise ZehnPackError("RELA relocations are not supported")
        if not isinstance(section, RelocationSection):
            continue

        relocated_name = section.name[4:]
        relocated_section = elf.get_section_by_name(relocated_name)
        if relocated_section is None or (relocated_section["sh_flags"] & 0x2) == 0:
            continue

        for reloc in section.iter_relocations():
            offset = reloc["r_offset"]
            reloc_type = reloc["r_info_type"]
            symbol_index = reloc["r_info_sym"]

            if symbol_index in undefined_symbols:
                if reloc_type == R_ARM_GOT_BREL:
                    got_entry_addr = struct.unpack_from("<I", exec_data, offset)[0]
                    undo_relocs.add(got_address + got_entry_addr)
                continue

            if reloc_type not in (R_ARM_ABS32, R_ARM_TARGET1):
                continue

            if offset & 0x3 and not saw_unaligned:
                relocs.append(pack_u24_record(RELOC_UNALIGNED, 0))
                saw_unaligned = True
            relocs.append(pack_u24_record(RELOC_ADD_BASE, offset))

    for offset in sorted(undo_relocs):
        relocs.append(pack_u24_record(RELOC_SET_ZERO, offset))

    return b"".join(relocs)


def build_flag_table(
    *,
    extra_data: bytearray,
    name: str,
    author: str | None,
    version: int,
    notice: str | None,
    ndless_min: int | None,
    ndless_max: int | None,
    ndless_rev_min: int | None,
    ndless_rev_max: int | None,
    color_support: bool,
    clickpad_support: bool,
    touchpad_support: bool,
    support_32mb: bool,
    hww_support: bool,
    uses_lcd_blit: bool,
) -> bytes:
    flags: list[bytes] = []

    flags.append(pack_u24_record(FLAG_EXECUTABLE_NAME, append_c_string(extra_data, name)))
    if author:
        flags.append(pack_u24_record(FLAG_EXECUTABLE_AUTHOR, append_c_string(extra_data, author)))
    flags.append(pack_u24_record(FLAG_EXECUTABLE_VERSION, version))
    if notice:
        flags.append(pack_u24_record(FLAG_EXECUTABLE_NOTICE, append_c_string(extra_data, notice)))
    if ndless_min is not None:
        flags.append(pack_u24_record(FLAG_NDLESS_VERSION_MIN, ndless_min))
    if ndless_rev_min is not None:
        flags.append(pack_u24_record(FLAG_NDLESS_REVISION_MIN, ndless_rev_min))
    if ndless_max is not None:
        flags.append(pack_u24_record(FLAG_NDLESS_VERSION_MAX, ndless_max))
    if ndless_rev_max is not None:
        flags.append(pack_u24_record(FLAG_NDLESS_REVISION_MAX, ndless_rev_max))

    flags.append(pack_u24_record(FLAG_RUNS_ON_COLOR, int(color_support)))
    flags.append(pack_u24_record(FLAG_RUNS_ON_CLICKPAD, int(clickpad_support)))
    flags.append(pack_u24_record(FLAG_RUNS_ON_TOUCHPAD, int(touchpad_support)))
    flags.append(pack_u24_record(FLAG_RUNS_ON_32MB, int(support_32mb)))
    flags.append(pack_u24_record(FLAG_USES_LCD_BLIT, int(uses_lcd_blit)))
    flags.append(pack_u24_record(FLAG_RUNS_ON_HWW, int(hww_support)))

    if len(extra_data) % 4:
        extra_data.extend(b"\0" * (4 - (len(extra_data) % 4)))

    return b"".join(flags)


def build_zehn_bytes(args: argparse.Namespace) -> tuple[bytes, dict[str, int | bool]]:
    input_path = Path(args.input)
    with input_path.open("rb") as handle:
        elf = ELFFile(handle)
        symtab, undefined_symbols, using_old_lcd_api, using_new_lcd_api = load_symtab(elf)
        exec_data, skipped_nobits, got_address = build_exec_image(elf, args.include_bss)

        if args.uses_lcd_blit is None:
            uses_lcd_blit = using_new_lcd_api and not using_old_lcd_api
        else:
            uses_lcd_blit = args.uses_lcd_blit

        if args.hww_support is None:
            hww_support = uses_lcd_blit
        else:
            hww_support = args.hww_support

        extra_data = bytearray()
        flag_bytes = build_flag_table(
            extra_data=extra_data,
            name=args.name,
            author=args.author,
            version=args.version,
            notice=args.notice,
            ndless_min=args.ndless_min,
            ndless_max=args.ndless_max,
            ndless_rev_min=args.ndless_rev_min,
            ndless_rev_max=args.ndless_rev_max,
            color_support=args.color_support,
            clickpad_support=args.clickpad_support,
            touchpad_support=args.touchpad_support,
            support_32mb=args.support_32mb,
            hww_support=hww_support,
            uses_lcd_blit=uses_lcd_blit,
        )
        reloc_bytes = build_reloc_table(
            elf,
            symtab,
            undefined_symbols,
            exec_data,
            got_address,
        )

    file_size = HEADER_STRUCT.size + len(reloc_bytes) + len(flag_bytes) + len(extra_data) + len(exec_data)
    alloc_size = file_size + skipped_nobits

    header = HEADER_STRUCT.pack(
        ZEHN_SIGNATURE,
        ZEHN_VERSION,
        file_size,
        len(reloc_bytes) // 4,
        len(flag_bytes) // 4,
        len(extra_data),
        alloc_size,
        struct.unpack("<I", struct.pack("<I", args.entry_offset_override or 0))[0]
        if args.entry_offset_override is not None
        else elf_entry(input_path),
    )
    meta = {
        "file_size": file_size,
        "alloc_size": alloc_size,
        "reloc_count": len(reloc_bytes) // 4,
        "flag_count": len(flag_bytes) // 4,
        "extra_size": len(extra_data),
        "exec_size": len(exec_data),
        "uses_lcd_blit": uses_lcd_blit,
        "hww_support": hww_support,
    }
    return header + reloc_bytes + flag_bytes + extra_data + bytes(exec_data), meta


def elf_entry(path: Path) -> int:
    with path.open("rb") as handle:
        return ELFFile(handle).header["e_entry"]


def write_outputs(zehn_bytes: bytes, args: argparse.Namespace) -> None:
    if args.zehn_output:
        Path(args.zehn_output).write_bytes(zehn_bytes)

    output_path = Path(args.output)
    if args.loader:
        loader_bytes = Path(args.loader).read_bytes()
        output_path.write_bytes(loader_bytes + zehn_bytes)
    else:
        output_path.write_bytes(zehn_bytes)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Input Ndless ELF file")
    parser.add_argument("--output", required=True, help="Output .zehn or wrapped .tns")
    parser.add_argument("--zehn-output", help="Optional path to also write the raw .zehn")
    parser.add_argument("--loader", help="Optional loader .tns to prepend")
    parser.add_argument("--name", required=True, help="Executable name")
    parser.add_argument("--author", help="Executable author")
    parser.add_argument("--version", type=int, default=1, help="Executable version integer")
    parser.add_argument("--notice", help="Executable notice string")
    parser.add_argument("--ndless-min", type=int, default=45, help="Minimum Ndless version * 10")
    parser.add_argument("--ndless-rev-min", type=int, help="Minimum Ndless revision")
    parser.add_argument("--ndless-max", type=int, help="Maximum Ndless version * 10")
    parser.add_argument("--ndless-rev-max", type=int, help="Maximum Ndless revision")
    parser.add_argument("--color-support", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--clickpad-support", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--touchpad-support", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--support-32mb", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--hww-support", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--uses-lcd-blit", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--include-bss", action="store_true", help="Embed NOBITS sections into the file")
    parser.add_argument("--entry-offset-override", type=lambda x: int(x, 0), help="Force a custom entry offset")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv or sys.argv[1:])
        zehn_bytes, meta = build_zehn_bytes(args)
        write_outputs(zehn_bytes, args)
    except ZehnPackError as exc:
        print(f"pack_zehn.py: {exc}", file=sys.stderr)
        return 1

    print(
        "Packed Zehn: "
        f"{meta['file_size']} bytes file, "
        f"{meta['alloc_size']} bytes alloc, "
        f"{meta['reloc_count']} relocs, "
        f"{meta['flag_count']} flags"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
