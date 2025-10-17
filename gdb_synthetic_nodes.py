"""
Helpers for creating/manipulating synthetic tag types for gdb pretty-printing.

The gdb is great, but to make pretty printers for types lacks the ability to
generate synthetic nodes to organise the data.  This module addresses that by
making values of types that don't make sense in the wild so are safe to use and
are capable of being reverted back to their original type quickly and easily.
"""

import gdb
import re

def make_enums_tag(val, enum_tuple):
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


def extract_enums_tag(val):
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
  type_str = str(val.type)
  matches = re.findall(r'\[(\d+)\]', type_str)
  return tuple(int(m) for m in matches)


def recover_value(val):
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
  i = str(val.type).index(' *(****)[')
  base_type = str(val.type)[0:i]
  return val.cast(gdb.lookup_type(base_type).pointer()).dereference()
