"""
Helpers for creating/manipulating synthetic tag types for gdb pretty-printing.

The gdb is great, but to make pretty printers for types lacks the ability to
generate synthetic nodes to organise the data.  This module addresses that by
making values of types that don't make sense in the wild so are safe to use and
are capable of being reverted back to their original type quickly and easily.
"""

import gdb
import re
from gdb_logger import log
from typing import Tuple

def make_enums_tag(val : gdb.Value, enum_tuple : Tuple[int, ...]) -> gdb.Value:
  """Create a synthetic tag value for gdb.

  The tag is encoded by building nested array types and producing a
  quad-pointer cast. This allows attaching a small integer tuple
  to a value so synthetic child nodes can be distinguished.

  Parameters
  ----------
  val : gdb.Value
    The original value (usually the object instance).
  enum_tuple : tuple[int, ...]
    Tuple of integers to encode in the synthetic tag.

  Returns
  -------
  gdb.Value
    The synthetic tag value (address casted to a pointer of a N dimensional
    array of quad-pointer type).
  """
  base_type = val.type.pointer()
  array_type = base_type
  for enum in reversed(enum_tuple):
    array_type = array_type.array(enum - 1)
  ptr_type = array_type.pointer().pointer().pointer().pointer()
  return val.address.cast(ptr_type)


def extract_enums_tag(val : gdb.Value) -> Tuple[int, ...]:
  """Extract encoded enum values from a synthetic tag value.

  Parameters
  ----------
  val : gdb.Value
    Synthetic tagged value.

  Returns
  -------
  tuple[int, ...]
    Tuple of decoded integers (in the same order they were encoded).
  """
  matched = get_type_tag_matches(str(val.type))
  matches = SYNTH_TAGS_RE.findall(matched.group('tags'))
  return tuple(int(m) for m in matches)

SYNTH_TAG_TYPE_RE = re.compile(r'^(?P<c>const )?(?P<v>volatile )?(?P<type>.+?)(?P<open>\()?\*\(\*\*\*\*\)' \
                          r'(?P<tags>(?:\[\d+\])+)(?(open)\))(?P<array>.*)$')

SYNTH_TAGS_RE = re.compile(r'\[(\d+)\]')

def get_type_tag_matches(type_str : str) -> re.Match[str]:
  """Gets a match object with the following groups:

  'c' - is the base type const
  'v' - is the base type volatile
  'type' - base type of the type
  'array' - any array items if any
  'tags' - A set of [] enclosed ints to state the enums.

  Parameters
  ----------
  type_str : string
      The stringified type. 

  Returns
  -------
  Match Object
      See description for what groups are available.

  Raises
  ------
  ValueError
      The type_str doesn't represent a tag type.

  """
  matches = SYNTH_TAG_TYPE_RE.search(type_str)
  if matches is None:
    raise ValueError(f"couldn't match tag type '{type_str}'")
  return matches

def recover_value(val : gdb.Value) -> gdb.Value:
  """Recover the original value from a synthetic tag.

  This retrieve the base type from the synthetic type representation used by
  make_enums_tag and returns the dereferenced original object value.

  Parameters
  ----------
  val : gdb.Value
    The synthetic tagged value.

  Returns
  -------
  gdb.Value
    The original value with its proper base type.
  """
  matched = get_type_tag_matches(str(val.type))
  base_type = gdb.lookup_type(matched.group('type'))
  if matched['v']:
    base_type = base_type.volatile()
  if matched['c']:
    base_type = base_type.const()
  log(f'type found: {base_type}')
  if matched['array']:
    array_sizes = SYNTH_TAGS_RE.findall(matched['array'])
    for array_size in reversed(array_sizes):
      base_type = base_type.array(array_size)
  return val.cast(base_type.pointer()).dereference()
