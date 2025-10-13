import re
import gdb

# Helpers for creating/manipulating synthetic tags using enum values

def log(msg):
  """Log a message to gdb console

  Parameters
  ----------
  msg : str
    The message to log
  """
  gdb.write(f"[LOGGER] {msg}\n", gdb.STDOUT)


def make_enums_tag(val, enum_tuple):
  """Create a synthetic tag value for gdb

  There can be multiple tags for a single value, so we create
  a synthetic type by making a quad pointer to an array of enum size.
  
  This allows us to distinguish between different synthetic nodes
  for the same base type.

  We'll start with <static>, <raw>, and <view> tags.  As there can be multiple
  views of the same data, we might add that tag as the 2nd enum value.
    
  May add more later such as <elements> to break up a large container into
  smaller chunks, though that may be a special case of a view, in which case
  we can just add to the 1st enum value instead of adding a 2nd enum value and
  use the 2nd enum value for chunking.

  Parameters
  ----------
  val : gdb.Value
    The original value
  enum_tuple : tuple[int, ...]
    Tuple of enum values to create the tag for

  Returns
  -------
  gdb.Value
    The synthetic tag value typed with the given enums.
  """
  # Create quad pointer type to array of enum size
  base_type = val.type.pointer()  # Get pointer to ColorRGBA
  array_type = base_type

  for enum in enum_tuple:
    array_type = array_type.array(enum-1)  # Make array of that pointer

  log(f"array_type: {array_type}\n")

  # The point of a quad pointer is to avoid conflicts with real pointers
  ptr_type = array_type.pointer().pointer().pointer().pointer()
  return val.address.cast(ptr_type)  # Cast address to quad pointer type

def extract_enums_tag(val):
  """Extract enum values from a synthetic tag value

  Parameters
  ----------
  val : gdb.Value
    The synthetic tag value

  Returns
  -------
  tuple[int, ...]
    Tuple of enum values extracted from the tag.
  """
  type_str = str(val.type)
  matches = re.findall(r'\[(\d+)\]', type_str)
  return tuple(int(m) for m in reversed(matches))

def recover_value(val):
  """Recover the original value from a synthetic tag

  Parameters
  ----------
  val : gdb.Value
    The synthetic tag value

  Returns
  -------
  gdb.Value
    The original value with the correct base type.
  """
  # TODO: Could be a better way to get base type
  i = str(val.type).index(' ')
  base_type = str(val.type)[0:i]
  # Dereference quad pointer to get to array of enum size
  return val.cast(gdb.lookup_type(base_type).pointer()).dereference()


# GDB pretty-printer example for ColorRGBA

class ComponentsNode:
  """Synthetic node for RGB components"""
  def __init__(self, val):
    # val is the original ColorRGBA, not a pointer
    self.val = val
  
  def children(self):
    yield 'red', self.val['r']
    yield 'green', self.val['g'] 
    yield 'blue', self.val['b']

  def to_string(self):
    return None

class AlphaNode:
  """Synthetic node for alpha channel"""
  def __init__(self, val):
    self.val = val
  
  def children(self):
    yield 'raw', self.val['a']
    yield 'normalized', gdb.Value(float(self.val['a']) / 255.0)

  def to_string(self):
    return None

class StatisticsNode:
  """Synthetic node for computed values"""
  def __init__(self, val):
    self.val = val
  
  def children(self):
    brightness = (int(self.val['r']) + int(self.val['g']) + int(self.val['b'])) / 3.0
    yield 'brightness', gdb.Value(float(brightness))
    yield 'opacity', gdb.Value(float(self.val['a']) / 255.0)

  def to_string(self):
    return None

class ColorRGBAPrinter:
  def __init__(self, val):
    self.val = val

  def children(self):
    yield 'Components', make_enums_tag(self.val, (0,))
    yield 'Alpha', make_enums_tag(self.val, (1,))
    yield 'Statistics', make_enums_tag(self.val, (2,))

  def to_string(self):
    return "ColorRGBA"

def lookup_type(val):
  log(f"type: {val.type}\n")
  type_str = str(val.type)
  
  # Match base type
  if type_str == 'ColorRGBA':
    return ColorRGBAPrinter(val)
  
  # Match synthetic node tags by looking for pointer pattern
  if '***' in type_str:
    enums = extract_enums_tag(val)
    log(f"enums: {enums}\n")
    if enums[0] == 0:
      return ComponentsNode(recover_value(val))
    elif enums[0] == 1:
      return AlphaNode(recover_value(val))
    elif enums[0] == 2:
      return StatisticsNode(recover_value(val))
  
  return None

gdb.pretty_printers.append(lookup_type)