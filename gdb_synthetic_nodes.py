# Helpers for creating/manipulating synthetic tags using enum values
import gdb
import re
from gdb_logger import log

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
    Tuple of small integers to encode in the synthetic tag.

  Returns
  -------
  gdb.Value
    The synthetic tag value (address casted to a quad-pointer type).
  """
  base_type = val.type.pointer()
  array_type = base_type
  for enum in reversed(enum_tuple):
    array_type = array_type.array(enum - 1)
  log(f"array_type: {array_type}\n")
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

  This attempts to retrieve the base type from the quad-pointer type
  representation used by make_enums_tag and returns the dereferenced
  original object value.

  Parameters
  ----------
  val : gdb.Value
    The synthetic tagged value.

  Returns
  -------
  gdb.Value
    The original value with its proper base type.
  """
  i = str(val.type).index(' ')
  base_type = str(val.type)[0:i]
  return val.cast(gdb.lookup_type(base_type).pointer()).dereference()
