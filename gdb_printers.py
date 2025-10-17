import gdb
import re

from gdb_logger import log
from gdb_synthetic_nodes import make_enums_tag, extract_enums_tag, recover_value

pretty_printers = {}
pretty_printers_re = []
def add_printer(type_name, printer):
  if re.search(r'^(?:::)?[a-zA-Z_][a-zA-Z_0-9]*(?:::[a-zA-Z_][a-zA-Z_0-9]*)*$', type_name):
    log(f"Adding exact printer for type: {type_name}")
    pretty_printers[type_name] = printer
  else:
    log(f"Adding regex printer for type: {type_name}")
    pretty_printers_re.append( (re.compile(type_name), printer) )

def match_printer(type_str):
  if type_str in pretty_printers:
    log(f"Exact match for type: {type_str}")
    return pretty_printers[type_str]
  for (regex, printer) in pretty_printers_re:
    if regex.match(type_str):
      log(f"Regex match for type: {type_str} with {regex.pattern}")
      return printer
  return None

# Synthetic node tags
# Each tag is a tuple of integers; the first integer indicates the kind of node.
STATIC_ENUM = (0,)
RAW_ENUM = (1,)
CHUNK_ENUM_ = 2
def CHUNK_ENUM(offset, chunk_size):
  return (2, offset, chunk_size)
VIEW_ENUM_ = 3
def VIEW_ENUM(view_index):
  return (VIEW_ENUM_ + view_index,)

def INCOMPLETE():
  raise NotImplementedError("INCOMPLETE: function not implemented yet")

def _unwrap_ptr_like(it):
  """
  If `it` is a pointer-like iterator from common lib impls, return its T*.
  - libstdc++: __gnu_cxx::__normal_iterator<T*, ...>  -> _M_current
  - libc++:    std::__wrap_iter<T*>                   -> __i
  - MSVC:      std::_Vector_iterator / _List_iterator -> _Ptr
  """
  t = it.type.strip_typedefs()
  if t.code == gdb.TYPE_CODE_PTR:
    return it
  for name in ('_M_current', '__i', '_Ptr'):
    try:
      p = it[name]
      if p.type.strip_typedefs().code == gdb.TYPE_CODE_PTR:
        return p
    except Exception:
      pass
  return None

def _is_pointer(v):
  return v.type.strip_typedefs().code == gdb.TYPE_CODE_PTR

def _to_int(v):
  try:
    return int(v)
  except Exception:
    try:
      return int(v.cast(gdb.lookup_type('long long')))
    except Exception:
      return int(str(v), 0)

def _has_random_access(begin, end):
  try:
    with GdbConvenienceVars(('pp_b', begin), ('pp_e', end)):
      # If both compile/evaluate, we're RA.
      gdb.parse_and_eval('$pp_e - $pp_b')
      gdb.parse_and_eval('$pp_b + 1')
      return True
  except gdb.error:
    return False

def _is_random_access(it):
  try:
    with GdbConvenienceVars(('pp_it', it)):
      gdb.parse_and_eval('$pp_it + 1')  # addition on a copy
      return True
  except gdb.error:
    return False

def _is_forward(it):
  try:
    with GdbConvenienceVars('pp_it', it):
      gdb.parse_and_eval('++$pp_it')  # pre-increment on a copy
      return True
  except gdb.error:
    return False
  
def _is_bidirectional(it):
  try:
    with GdbConvenienceVars('pp_it', it):
      gdb.parse_and_eval('--$pp_t')   # pre-decrement on a copy
      return True
  except gdb.error:
    return False

# gdb_printers.py (top-level helper)
def call0(val, method):
  with GdbConvenienceVars(('pp_self', val)):
    return int(gdb.parse_and_eval(f'$pp_self.{method}()'))

def get_member_value(val, member_name, adjust_return_type=lambda t: t):
  """Get value from member.  If member is a function, execute function with 0
     parameters and get that value.

  Parameters
  ----------
  val : gdb.Value
      Value to get value from.
  member_name : string
      Name of member to get value from.
  adjust_return_type : function, optional
      Function to adjust the return type of the member function, by default
      returns the value unchanged.  Only affects if value is retrieved from
      object.
  """
  member = None
  try:
    member = val[member_name]
    if member.type.strip_typedefs().code == gdb.TYPE_CODE_FUNC:
      try:
        log(f"get_member_value calling function member {member_name}()")
        return adjust_return_type(member())
      except Exception as e:
        log(f"get_member_value call exception: {e}")
        return None
  except Exception as e:
    # Fallback: call it directly, avoids "address of method" on overloads
    log(f"get_member_value exception: {e} ; trying call {member_name}()")
    try:
      gdb.set_convenience_variable('$_self', val)
      res = gdb.parse_and_eval(f'$_self.{member_name}()')
      return adjust_return_type(res)
    except Exception as e2:
      log(f"get_member_value fallback exception: {e2}")
      try:
        return adjust_return_type(call0(val, member_name))
      except Exception as e3:
        log(f"get_member_value call0 exception: {e3}")
        return None
    finally:
      # setting to None avoids convenience variable from leaking into other calls
      gdb.set_convenience_variable('$_self', None)

def get_c_range_and_size(val, begin_member_name, end_member_name, size_member_name=None):
  """ Get (begin, end) gdb.Values from a container-like `val` """
  begin = get_member_value(val, begin_member_name)
  end = get_member_value(val, end_member_name)
  size = get_member_value(val, size_member_name) if size_member_name is not None else None

  if size is not None:
    return (begin, end, size)
  
  return (begin, end)

class GdbConvenienceVars:
  """Scoped convenience vars: sets on enter, restores/removes on exit."""

  def __init__(self, *name_value_pairs):
    """Use within a with statement to setup gdb convenience variables by
       passing in a list of (name, gdb.Value) pairs to initialise them.  On
       exiting the with statement, the gdb convenience variables will be
       restored to what they were prior to the execution of the block.

    Raises
    ------
    ValueError
        Convenience variables should not start with a $.  Prefixing with a $ is
        only needed within a gdb.parse_and_eval() parameter list.
    """
    self._saved = []

    for name, value in name_value_pairs:
      if name[0] == "$":
        raise ValueError("Convenience variable shouldn't start with $ unless used in gdb.parse_and_eval()")
      prev = gdb.convenience_variable(name)  # None if not set
      self._saved.append((name, prev))
      gdb.set_convenience_variable(name, value)

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    for name, prev in self._saved:
      gdb.set_convenience_variable(name, prev)

def emit_chunked_elements(c_range_and_size, chunk_size=16):
  """Emit elements of a pointer or iterator in chunks of given size.

  Parameters
  ----------
  c_range : tuple[gdb.Value, gdb.Value] | tuple[gdb.Value, gdb.Value, int]
      Pair of (begin, end) gdb.Values representing a range of elements or
      (begin, end, size) where `size` is the number of elements in the range.
  chunk_size : int, optional
      Maximum chunk size, by default 16

  Yields
  ------
  tuple[str, gdb.Value]
      Tuple of chunk range description and corresponding synthetic tag
  """
  begin, end = c_range_and_size
  size = None
  if len(c_range_and_size) > 2:
    size = c_range_and_size[2]

  try:
    # 1) Pointer-like fast path (unwrap if possible)
    b_ptr = _unwrap_ptr_like(begin)
    e_ptr = _unwrap_ptr_like(end)
    if b_ptr is not None and e_ptr is not None:
      length = int(size) if size is not None else _to_int(e_ptr - b_ptr)
      for i in range(0, length, chunk_size):
        n = chunk_size if i + chunk_size <= length else (length - i)
        yield f'[{i}..{i+n-1}]', make_enums_tag(b_ptr + i, CHUNK_ENUM(i, n))
      return

    # 2) Random-access iterators via C++ evaluator
    if _has_random_access(begin, end):
      with GdbConvenienceVars(('_b', begin), ('_e', end)):
        # gdb.set_convenience_variable('$_b', begin)
        # gdb.set_convenience_variable('$_e', end)
        length = int(size) if size is not None else _to_int(gdb.parse_and_eval('$_e - $_b'))
        for i in range(0, length, chunk_size):
          n = chunk_size if i + chunk_size <= length else (length - i)
          it_i = gdb.parse_and_eval(f'$_b + {i}')
          yield f'[{i}..{i+n-1}]', make_enums_tag(it_i, CHUNK_ENUM(i, n))
      return

    # 3) Forward / bidirectional: scan from begin to end (or until `size`)
    with GdbConvenienceVars(('_it', begin), ('_end', end)):
      # gdb.set_convenience_variable('$_it', begin)
      # gdb.set_convenience_variable('$_end', end)

      def _at_end():
        # If the iterator type doesn't support ==, this will throw; we then
        # rely purely on `size`.
        try:
          return bool(_to_int(gdb.parse_and_eval('($_it == $_end)')))
        except gdb.error:
          return False

      i = 0
      have_size = size is not None
      total = int(size) if have_size else None

      while have_size and i < total or not have_size and not _at_end():
        start_it = gdb.parse_and_eval('$_it')  # snapshot at chunk start
        steps = 0
        limit = chunk_size if not have_size else min(chunk_size, total - i)

        # advance up to `limit` or until `end`
        while steps < limit:
          if not have_size and _at_end():
            break
          gdb.parse_and_eval('++$_it')
          steps += 1

        if steps == 0:  # nothing advanced (size too big or already at end)
          break

        yield f'[{i}..{i+steps-1}]', make_enums_tag(start_it, CHUNK_ENUM(i, steps))
        i += steps

  except gdb.error as e:
    log(f'emit_chunked_elements gdb.error: {e}')
  except Exception as e:
    log(f'emit_chunked_elements exception: {e}')

def emit_elements(it, offset, size):
  """ Emit elements of an iterator one by one, up to `size` elements,
      starting at `offset` """
  try:
    gdb.set_convenience_variable('$_it', it)
    i = 0

    # emit up to size elements
    while i < size:
      yield f'[{offset + i}]', gdb.parse_and_eval('$_it').dereference()
      gdb.parse_and_eval('++$_it')
      i += 1

  except gdb.error as e:
    log(f'emit_elements gdb.error: {e}')
  except Exception as e:
    log(f'emit_elements exception: {e}')

def summary(named=False, show_type=True):
  def named_summary(v):
    summary = f"{{r={int(v['r'])}, g={int(v['g'])}, b={int(v['b'])}, a={int(v['a'])}}}"
    if show_type:
      summary = f"{v.type} {summary}"
    return summary
  
  def unnamed_summary(v):
    summary = f"{{{int(v['r'])}, {int(v['g'])}, {int(v['b'])}, {int(v['a'])}}}"
    if show_type:
      summary = f"{v.type} {summary}"
    return summary
  
  return named_summary if named else unnamed_summary

def emit_raw_children(val):
  """ Emit raw children of a gdb.Value if possible """
  try:
    for field in val.type.fields():
      if getattr(field, "is_base_class", False):
        yield f"{field.name} (base)", val.cast(gdb.lookup_type(field.name).reference())
      elif getattr(field, "bitpos", None) is not None:
        yield field.name, val[field.name] # non-static field
  except Exception as e:
    log(f"emit_raw_children exception: {e}")
    return

def emit_static_children(val):
  """ Emit static children of a gdb.Value if any """
  try:
    for field in val.type.fields():
      if getattr(field, "bitpos", None) is not None:
        continue
      yield field.name, val[field.name]
    return
  except Exception as e:
    log(f"emit_raw_children exception: {e}")
    return
  
  t = val.type.strip_typedefs()
  qual = t.tag or t.name
  if not qual:
    return

  for f in t.fields():
    if getattr(f, "is_base_class", False):
      continue
    
    # Prefer is_static when available; otherwise fall back to "no bitpos ⇒ static"
    is_static = getattr(f, "is_static", None)
    if is_static is None:
      is_static = getattr(f, "bitpos", None) is None
    if not is_static or not f.name:
      continue

    fullname = f"{qual}::{f.name}"
    v = None
    try:
      sym = gdb.lookup_global_symbol(fullname)
      if sym:
        v = sym.value()
    except gdb.error:
      pass
    if v is None:
      try:
        v = gdb.parse_and_eval(fullname)
      except gdb.error:
        v = None

    yield f.name, v

def has_static(val):
  try:
    for field in val.type.fields():
      if getattr(field, "bitpos", None) is None:
        return True
  except Exception as e:
    log(f"has_static exception for {val.type}: {e}")
  return False

  t = val.type.strip_typedefs()
  qual = t.tag or t.name
  if not qual:
    return

  for f in t.fields():
    if getattr(f, "is_base_class", False):
      continue
    
    # Prefer is_static when available; otherwise fall back to "no bitpos ⇒ static"
    is_static = getattr(f, "is_static", None)
    if is_static is None:
      is_static = getattr(f, "bitpos", None) is None
    if not is_static or not f.name:
      continue

    return True
  return False

class DefaultPrinter:
  """Handler for default pretty-printing of structs/unions/classes"""
  def __init__(self, val, printer = None):
    self.val = val
    self.printer = printer

  def get_view_named(self, name):
    if self.printer is not None and 'views' in self.printer:
      for view in self.printer['views']:
        if view['name'] == name:
          return view
    return None
  
  def to_string(self):
    view_count = self.count_views()
    if self.printer is not None and 'default_view' in self.printer:
      default_view_name = self.printer['default_view']
      default_view = self.get_view_named(default_view_name)
    else:
      default_view_name = default_view = None

    if view_count == 0 or default_view_name is None:
      # no views, or no default_view: just show raw type's summary
      if self.printer is not None and 'summary' in self.printer and callable(self.printer['summary']):
        return self.printer['summary'](self.val)
    else:
      # one default view: show that view's summary if any
      if default_view is None:
        return f'<default_view "{default_view_name}" not defined>'
      if 'summary' in default_view and callable(default_view['summary']):
        return default_view['summary'](self.val)
    # view has no summary, or no views/printer: show nothing
    return ""
  
  def count_views(self):
    if self.printer is not None and 'views' in self.printer:
      log(f"count_views for {self.val.type} = {len(self.printer['views'])}")
      return len(self.printer['views'])
    return 0

  def view_name(self, index):
    if self.printer is not None and 'views' in self.printer and index < len(self.printer['views']):
      return self.printer['views'][index]['name']
    return f"<view {index}>"
  
  def children(self):
    log(f"DefaultPrinter children for {self.val.type}\n{self.printer}")

    view_count = self.count_views()
    if self.printer is not None and 'default_view' in self.printer:
      default_view_name = self.printer['default_view']
      default_view = self.get_view_named(default_view_name)
    else:
      default_view_name = default_view = None

    # Show top-level view
    log(f"count_views for {self.val.type} = {view_count}")
    if view_count == 0 or default_view_name is None:
      # show raw members at top-level if no views or no default_view
      log(f"top-level view for {self.val.type} = <Raw>")
      yield from RawPrinter(self.val).children()
    else:
      # show default view at top-level
      if default_view is None:
        yield f'<default_view "{default_view_name}" not defined>', \
          self.val.cast(self.val.type.array(-1)) # prevents showing assignment or expanding node
      else:
        log(f"top-level view for {self.val.type} = {default_view_name}")
        yield from ViewPrinter(self.val, default_view).children()

    log("Continuing to other views...\n")
    
    # Show static/raw/views views
    if has_static(self.val):
      log(f"has_static for {self.val.type}")
      yield '<Static>', make_enums_tag(self.val, STATIC_ENUM)

    if view_count > 0:
      if default_view_name is not None:
        # show <Raw> tag if there are views and a default_view
        yield '<Raw>', make_enums_tag(self.val, RAW_ENUM)

      # show views other than default_view
      for i in range(view_count):
        if self.view_name(i) != default_view_name:
          yield f"<{self.view_name(i)}>", make_enums_tag(self.val, VIEW_ENUM(i))

class StaticPrinter:
  """Handler for static members only"""
  def __init__(self, val):
    self.val = val

  def children(self):
    yield from emit_static_children(self.val)

  def to_string(self):
    return ""

class RawPrinter:
  """Handler for raw members only"""
  def __init__(self, val):
    self.val = val

  def children(self):
    yield from emit_raw_children(self.val)

  def to_string(self):
    return ""
  
class ViewPrinter:
  """Handler for a specific view of members"""
  def __init__(self, val, view):
    self.val = val
    self.view = view

  def children(self):
    if 'node' in self.view:
      log(f"ViewPrinter node for {self.val.type} = {self.view['node']}\n")
      node = self.view['node'](self.val)
      yield from node.children()
    elif 'nodes' in self.view:
      log(f"ViewPrinter raw nodes for {self.val.type}\n")
      nodes = self.view['nodes']
      for i in range(0, len(nodes), 2):
        name = nodes[i]
        func = nodes[i+1]
        log(f"  node {name} = {func(self.val)}\n")
        try:
          yield name, func(self.val)
        except Exception as e:
          log(f"ViewPrinter child exception: {e}")
          yield name, "<error>"
      if 'elements' in self.view:
        log(f"ViewPrinter elements for {self.val.type}\n")
        yield from self.view['elements'](self.val)
    return
  
  def to_string(self):
    if 'summary' in self.view and self.view['summary']:
      return self.view['summary'](self.val)
    return getattr(self.view, 'summary', '')

class ChunkPrinter:
  """Handler for chunked elements"""
  def __init__(self, val, offset, chunk_size):
    self.val = val
    self.offset = offset
    self.chunk_size = chunk_size

  def children(self):
    yield from emit_elements(self.val, self.offset, self.chunk_size)

  def to_string(self):
    return ""

def _lookup_type(val):
  type_str = str(val.type)
  log(f"type: {type_str}")
  
  printer = match_printer(type_str)
  if printer is not None:
    log(f"Matched printer for type: {type_str}")
    return DefaultPrinter(val, printer)
  
  if val.type.code in (gdb.TYPE_CODE_STRUCT, gdb.TYPE_CODE_UNION):
    log(f"DefaultPrinter for struct/union type: {type_str}")
    return DefaultPrinter(val)
  
  # Match synthetic node tags by looking for pointer pattern
  if '(****)' in type_str:
    enums = extract_enums_tag(val)
    actual_val = recover_value(val)
    base_key = str(actual_val.type)

    log(f"enums: {enums}")
    if enums == STATIC_ENUM:
      log(f"StaticPrinter for {actual_val.type}")
      return StaticPrinter(actual_val)
    elif enums == RAW_ENUM:
      log(f"RawPrinter for {actual_val.type}")
      return RawPrinter(actual_val)
    elif enums[0] == CHUNK_ENUM_:
      log(f"ChunkPrinter for {actual_val.type} chunk size {enums[1]}")
      return ChunkPrinter(actual_val, offset=enums[1], chunk_size=enums[2])
    else:
      # everything else is a view
      view_index = enums[0] - VIEW_ENUM_
      log(f"ViewPrinter index {view_index} for {actual_val.type}")
      return ViewPrinter(actual_val, pretty_printers[base_key]["views"][view_index])
      
  log(f"No printer found for type: {type_str}")
  return None

def disable_all_printers():
  """ Disable all existing pretty-printers in gdb.pretty_printers and
      gdb.objfiles().pretty_printers """
  log("Disabling existing pretty-printers")
  log(f"Current pretty-printers: {gdb.pretty_printers}")
  # Disable all global/prgspace printers
  for pp in gdb.pretty_printers:
    log(f"Global printer: {pp}")
    if hasattr(pp, "enabled"):
      log(f"Disabling global printer: {pp}")
      pp.enabled = False

  # Disable all printers attached to each objfile
  for obj in gdb.objfiles():
    for pp in getattr(obj, "pretty_printers", []):
      if hasattr(pp, "enabled"):
        log(f"Disabling objfile printer: {pp}")
        pp.enabled = False

  log("All existing pretty-printers disabled")

# TODO: Get iterator chunking working
# disable_all_printers()

# log("Enabling custom pretty-printers\n")
# gdb.pretty_printers.append(_lookup_type)

# add_printer('std::vector<.*>', {
#   'summary': lambda v: "std::vector", #f"std::vector(size={get_member_value(v, 'size', int)} capacity={get_member_value(v, 'capacity', int)})",
#   'views': [
#     {
#       'name': 'Elements',
#       'elements': lambda v: emit_chunked_elements(get_c_range_and_size(v.cast(gdb.lookup_type("std::_Vector_base<int, std::allocator<int> >")), '_M_start', '_M_finish'), chunk_size=16)
#     },
#   ]
# })