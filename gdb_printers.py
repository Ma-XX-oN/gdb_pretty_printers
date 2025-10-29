"""
Framework to make a cohesive, easy to use pretty printer for gdb.  This uses
synthetic nodes to group data together into static, raw, and other views making
it easier to read and find information.
"""
import gdb
import re
import traceback

from typing import Tuple, TypedDict, Required, NotRequired, Callable, Optional,\
   TypeVar, Protocol, cast, overload
from collections.abc import Iterator
from gdb_logger import log
from gdb_synthetic_nodes import make_enums_tag, extract_enums_tag, recover_value

MAX_SUMMARY_LEN = 100

class PrinterLike(Protocol):
    def children(self) -> Iterator[tuple[str, gdb.Value]]: ...
    @overload
    def to_string(self) -> str: ...
    @overload
    def to_string(self, max_len : int = MAX_SUMMARY_LEN, /) -> str: ...

ValuePrinterClass = TypeVar("ValuePrinterClass", bound = PrinterLike, covariant=True)

class PrinterCtor(Protocol[ValuePrinterClass]):
  def __call__(self, val : gdb.Value, /) -> ValuePrinterClass: ...

class View(TypedDict):
  name: Required[str]
  summary: NotRequired[Callable[[gdb.Value, int], str] | str]
  node: NotRequired[PrinterCtor[PrinterLike]]
  nodes: NotRequired[Tuple[Tuple[str, Callable[[gdb.Value], str | gdb.Value]], ...]]
  
class Printer(TypedDict):
  summary: NotRequired[Callable[[gdb.Value, int], str] | str]
  default_view: NotRequired[str]
  views: NotRequired[Tuple[View, ...]]

_pretty_printers = {}
_pretty_printers_re = []


def add_printer(type_name, printer : Printer):
  """Add a new printer by specifying a structure.

  Parameters
  ----------
  type_name : string
    The type name to match against to pretty print

  printer : dict
      "summary" - lambda(v) : string (optional)
        - Raw summary.
        - Can call summary() to generate a default summary of all elements.
        - If not specified, will show nothing for the summary.
      "views" - list<dict> (optional)
        - Each dict has the following members:
          "name" - string (optional)
            - Name of the view.  Node will have that name with <> around it.
            - Recommend to at least capitalise first letter.
            - If not specified, will show as <View N> where N is the view index.
          "summary" - lambda(v) : string (optional)
            - View summary.
            - Can call summary() to generate a default summary of all elements.
            - If not specified, will show nothing for the summary.
          "nodes" - list (optional)
            - An even number of elements, where:
              - 1st element is the name of the element, and
              - 2nd element is a lambda(v) : {gdb.Value | any}
          "node" - class object (optional)
            - For complex views, it may be necessary to write a full blown class
              pretty printer.
            - Specifying this will prevent "nodes" item from being looked at.
        "default_view" - string (optional)
          - Name of view to show at top level.
          - If not specified, the raw view will be at top level.
  """
  log(f"Adding exact printer for type: {type_name}")
  _pretty_printers[type_name] = printer

def add_re_printer(type_re, printer : Printer):
  """Add a new printer by specifying a structure.

  Parameters
  ----------
  type_name : string
    The regex to match against the type to pretty print

  printer : dict
      "summary" - lambda(v) : string (optional)
        - Raw summary.
        - Can call summary() to generate a default summary of all elements.
        - If not specified, will show nothing for the summary.
      "views" - list<dict> (optional)
        - Each dict has the following members:
          "name" - string (optional)
            - Name of the view.  Node will have that name with <> around it.
            - Recommend to at least capitalise first letter.
            - If not specified, will show as <View N> where N is the view index.
          "summary" - lambda(v) : string (optional)
            - View summary.
            - Can call summary() to generate a default summary of all elements.
            - If not specified, will show nothing for the summary.
          "nodes" - list (optional)
            - An even number of elements, where:
              - 1st element is the name of the element, and
              - 2nd element is a lambda(v) : {gdb.Value | any}
          "node" - class object (optional)
            - For complex views, it may be necessary to write a full blown class
              pretty printer.
            - Specifying this will prevent "nodes" item from being looked at.
        "default_view" - string (optional)
          - Name of view to show at top level.
          - If not specified, the raw view will be at top level.
  """
  log(f"Adding regex printer for type: {type_re}")
  _pretty_printers_re.append( (re.compile(type_re), printer) )

def _match_printer(type_str : str):
  if type_str in _pretty_printers:
    log(f"Exact match for type: {type_str}")
    return _pretty_printers[type_str]
  for (regex, printer) in _pretty_printers_re:
    if regex.match(type_str):
      log(f"Regex match for type: {type_str} with {regex.pattern}")
      return printer
  return None

# Synthetic node tags
# Each tag is a tuple of integers; the first integer indicates the kind of node.
_MSG_ENUM_I = 0
def _MSG_ENUM(string : str) -> Tuple[int, ...]:
  return (_MSG_ENUM_I, *list(string.encode("utf-8")))
_STATIC_ENUM_I = 1
_STATIC_ENUM = (_STATIC_ENUM_I,)
_RAW_ENUM_I = 2
_RAW_ENUM = (_RAW_ENUM_I,)
_CHUNK_ENUM_I = 3
def _CHUNK_ENUM(offset : int, chunk_size : int) -> Tuple[int, ...]:
  return (_CHUNK_ENUM_I, offset, chunk_size)
_VIEW_ENUM_I = 4
def _VIEW_ENUM(view_index : int) -> Tuple[int, ...]:
  return (_VIEW_ENUM_I + view_index,)

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
  for name in ("_M_current", "__i", "_Ptr"):
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
      return int(v.cast(gdb.lookup_type("long long")))
    except Exception:
      return int(str(v), 0)

def _has_random_access(begin, end):
  try:
    with GdbConvenienceVars(("pp_b", begin), ("pp_e", end)):
      # If both compile/evaluate, we're RA.
      gdb.parse_and_eval("$pp_e - $pp_b")
      gdb.parse_and_eval("$pp_b + 1")
      return True
  except gdb.error:
    return False

def _is_random_access(it):
  try:
    with GdbConvenienceVars(("pp_it", it)):
      gdb.parse_and_eval("$pp_it + 1")  # addition on a copy
      return True
  except gdb.error:
    return False

def _is_forward(it):
  try:
    with GdbConvenienceVars("pp_it", it):
      gdb.parse_and_eval("++$pp_it")  # pre-increment on a copy
      return True
  except gdb.error:
    return False

def _is_bidirectional(it):
  try:
    with GdbConvenienceVars("pp_it", it):
      gdb.parse_and_eval("--$pp_t")   # pre-decrement on a copy
      return True
  except gdb.error:
    return False

def call0(val, method):
  with GdbConvenienceVars(("pp_self", val)):
    return int(gdb.parse_and_eval(f"$pp_self.{method}()"))

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
        log(f"get_member_value call exception: {e}\n  {traceback.format_exc()}")
        return None
  except Exception as e:
    # Fallback: call it directly, avoids "address of method" on overloads
    log(f"get_member_value exception: {e} ; trying call {member_name}()")
    try:
      with GdbConvenienceVars(("pp_self", val)):
        res = gdb.parse_and_eval(f"$pp_self.{member_name}()")
        return adjust_return_type(res)
    except Exception as e2:
      log(f"get_member_value fallback exception: {e2}")
      try:
        return adjust_return_type(call0(val, member_name))
      except Exception as e3:
        log(f"get_member_value call0 exception: {e3}")
        return None

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

    Parameters
    ----------
    name_value_pairs : List[ Tuple[string, gdb.Value] ]
        A list of name value tuples to use as convenience variables.

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
        yield f"[{i}..{i+n-1}]", make_enums_tag(b_ptr + i, _CHUNK_ENUM(i, n))
      return

    # 2) Random-access iterators via C++ evaluator
    if _has_random_access(begin, end):
      with GdbConvenienceVars(("_b", begin), ("_e", end)):
        length = int(size) if size is not None else _to_int(gdb.parse_and_eval("$_e - $_b"))
        for i in range(0, length, chunk_size):
          n = chunk_size if i + chunk_size <= length else (length - i)
          it_i = gdb.parse_and_eval(f"$_b + {i}")
          yield f"[{i}..{i+n-1}]", make_enums_tag(it_i, _CHUNK_ENUM(i, n))
      return

    # 3) Forward / bidirectional: scan from begin to end (or until `size`)
    with GdbConvenienceVars(("_it", begin), ("_end", end)):
      def _at_end():
        # If the iterator type doesn't support ==, this will throw; we then
        # rely purely on `size`.
        try:
          return bool(_to_int(gdb.parse_and_eval("($_it == $_end)")))
        except gdb.error:
          return False

      i = 0
      have_size = size is not None
      total = int(size) if have_size else None

      while have_size and i < total or not have_size and not _at_end():
        start_it = gdb.parse_and_eval("$_it")  # snapshot at chunk start
        steps = 0
        limit = chunk_size if not have_size else min(chunk_size, total - i)

        # advance up to `limit` or until `end`
        while steps < limit:
          if not have_size and _at_end():
            break
          gdb.parse_and_eval("++$_it")
          steps += 1

        if steps == 0:  # nothing advanced (size too big or already at end)
          break

        yield f"[{i}..{i+steps-1}]", make_enums_tag(start_it, _CHUNK_ENUM(i, steps))
        i += steps

  except gdb.error as e:
    log(f"emit_chunked_elements gdb.error: {e}\n  {traceback.format_exc()}")
  except Exception as e:
    log(f"emit_chunked_elements exception: {e}\n  {traceback.format_exc()}")

def emit_elements(it, offset, size):
  """ Emit elements of an iterator one by one, up to `size` elements,
      showing starting at `offset` """
  try:
    with GdbConvenienceVars(("pp_it", it)):
      i = 0

      # emit up to size elements
      while i < size:
        yield f"[{offset + i}]", gdb.parse_and_eval("$pp_it").dereference()
        gdb.parse_and_eval("++$pp_it")
        i += 1

  except gdb.error as e:
    log(f"emit_elements gdb.error: {e}\n  {traceback.format_exc()}")
  except Exception as e:
    log(f"emit_elements exception: {e}\n  {traceback.format_exc()}")

import inspect
from inspect import Parameter

def arity(fn):
  """Return (min_positional, max_positional, required_kwonly, has_varargs, has_varkw)."""
  sig = inspect.signature(fn)
  min_pos = max_pos = 0
  has_varargs = has_varkw = False
  required_kwonly = set()

  for p in sig.parameters.values():
    k = p.kind
    if k in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD):
      if p.default is Parameter.empty:
        min_pos += 1
      max_pos += 1
    elif k is Parameter.VAR_POSITIONAL:
      has_varargs = True
    elif k is Parameter.KEYWORD_ONLY:
      if p.default is Parameter.empty:
        required_kwonly.add(p.name)
    elif k is Parameter.VAR_KEYWORD:
      has_varkw = True

  if has_varargs:
    max_pos = float("inf")
  return min_pos, max_pos, required_kwonly, has_varargs, has_varkw

CHAR_TYPES = (gdb.lookup_type("char"), gdb.lookup_type("unsigned char"))
USER_TYPE_CODES = (gdb.TYPE_CODE_STRUCT, gdb.TYPE_CODE_UNION)

def summary(named=False, show_type=True, show_char_as_int=True):
  """Returns a function that will output the values of the members as a braced,
     comma separated list.

  Parameters
  ----------
  named : bool, optional
      States if the fields are to be named or not, by default False
  show_type : bool, optional
      States if to show the type of the object before the fields, by default True
  show_char_as_int : bool, optional
      States if chars are to be displayed as integers (without the character it
      represent showing up after the numeric value).

  """
  summary = ""
  def v_to_str(v : gdb.Value, max_len : int):
    printerObj = cast(Optional[PrinterLike], gdb.default_visualizer(v))
    if printerObj and arity(printerObj.to_string)[1] == 1:
      # subtracting 2 because there is an implicit ", " that is put before this.
      return printerObj.to_string(max(0, max_len - len(summary) - 2))
    else:
      return str(v)

  if show_char_as_int:
    def val_to_str(v : gdb.Value, max_len : int): # type: ignore[reportRedeclaration]
      return str( _to_int(v) if v.type in CHAR_TYPES else v_to_str(v, max_len) )
  else:
    def val_to_str(v : gdb.Value, max_len : int):
      return v_to_str(v, max_len)

  if named:
    def field_entry(v : gdb.Value, f : gdb.Field, max_len : int): # type: ignore[reportRedeclaration]
      if f.name is None:
        return "* unnamed field *"
      log(f"name: {f.name} type: {v[f.name].type}")
      return f.name + "=" + str(val_to_str(v[f.name], max_len))
  else:
    def field_entry(v : gdb.Value, f : gdb.Field, max_len : int):
      if f.name is None:
        return "* unnamed field *"
      return val_to_str(v[f.name], max_len)
    
  def summary_fn(val : gdb.Value, max_len : int = MAX_SUMMARY_LEN) -> str:
    nonlocal summary
    try:
      fields = val.type.fields()
      summary = "{ "
      field_count = 0
      # get first non base class/static member
      i = 0
      for i in range(len(fields)):
        field = fields[i]
        if (getattr(field, "is_base_class", False) is False and getattr(field, "bitpos", False)) is not False:
          log(f"first: {field.name} len: {len(summary)} max_len: {max_len}")
          if len(summary) >= max_len:
            summary = "{...}"
            log("aborting...")
            break
          summary += str(field_entry(val, field, max_len))
          field_count += 1
          break

      if summary != "{...}":
        # get rest non base class/static members
        for field_i in range(i+1, len(fields)):
          field = fields[field_i]
          if (getattr(field, "is_base_class", False) is False and getattr(field, "bitpos", False)) is not False:
            log(f"[{field_i}]: {field.name} len: {len(summary)} max_len: {max_len}")
            if len(summary) >= max_len:
              summary += ", ..."
              log("aborting...")
              break
            summary += ", " + field_entry(val, field, max_len)
            field_count += 1

        if (field_count):
          summary += " }"
        else:
          summary = "{}"
    except Exception as e:
      log(f"summary exception: {e}\n  {traceback.format_exc()}")
      return f"<summary exception: {e}>"

    if show_type:
      summary = f"{val.type} {summary}"
    return summary

  return summary_fn

def emit_raw_children(val : gdb.Value):
  """ Emit raw children of a gdb.Value if possible """
  try:
    if val.type.code != gdb.TYPE_CODE_ARRAY:
      for field in val.type.fields():
        if field.name is None:
          continue # unnamed field
        if getattr(field, "is_base_class", False):
          yield f"{field.name} (base)", val.cast(gdb.lookup_type(field.name).reference())
        elif getattr(field, "bitpos", None) is not None:
          yield field.name, val[field.name] # non-static field
  except Exception as e:
    log(f"emit_raw_children exception: {e}\n  {traceback.format_exc()}")
    return

def emit_static_children(val : gdb.Value):
  """ Emit static children of a gdb.Value if any """
  try:
    if val.type.code != gdb.TYPE_CODE_ARRAY:
      for field in val.type.fields():
        if field.name is None:
          continue # unnamed field
        if getattr(field, "bitpos", None) is not None:
          continue # non-static field
        yield field.name, val[field.name]
    return
  except Exception as e:
    log(f"emit_raw_children exception: {e}\n  {traceback.format_exc()}")
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

def has_static(val : gdb.Value):
  try:
    for field in val.type.fields():
      if getattr(field, "bitpos", None) is None:
        return True
  except Exception as e:
    log(f"has_static exception for {val.type}: {e}\n  {traceback.format_exc()}")
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

class MessagePrinter(gdb.ValuePrinter):
  """Allows a message to be on the rhs of =.  Not very useful I think as
     ArrayPrinter will do the same with a python string. Leaving here for now.
  """
  def __init__(self, val : gdb.Value, string : str) -> None:
    self.val = val
    self.string = string

  def num_children(self):
    # Hmmmm. Never called.
    log("Getting num_children")
    return 0

  def children(self):
    yield "Parent node is a message", None

  def to_string(self, _ : int = MAX_SUMMARY_LEN):
    return self.string

def _get_summary(summary : str | Callable[[gdb.Value], str] | Callable[[gdb.Value, int], str], val : gdb.Value, max_len : int) -> str:
  """Gets the summary string as a string.

  Parameters
  ----------
  summary : str | Callable[[gdb.Value], str] | Callable[[gdb.Value, int], str]
      A string representing the summary or a function that generates it.
  val : gdb.Value
      The value to generate a summary for.
  max_len : int
      The soft maximum number of characters to stop getting more summary data
      from `val`.

  Returns
  -------
  str
      Summary string.  If the type creates more characters than `max_len`,
      finishes that field element, adds a ... to the end and stops processing
      the rest of the fields.
  """
  if callable(summary):
    if arity(summary)[1] == 2:
      summary = cast(Callable[[gdb.Value, int], str], summary)
      return summary(val, max_len)
    else:
      summary = cast(Callable[[gdb.Value], str], summary)
      return summary(val)            
  else:
    return summary

class DefaultPrinter(gdb.ValuePrinter):
  """Handler for default pretty-printing of structs/unions/classes"""
  def __init__(self, val : gdb.Value, printer : Optional[Printer] = None):
    self.val = val
    self.printer = printer

  def get_view_named(self, name : str):
    if self.printer is not None and "views" in self.printer:
      for view in self.printer["views"]:
        if view["name"] == name:
          return view
    return None

  def to_string(self, max_len : int = MAX_SUMMARY_LEN):
    if max_len <= 0:
      return "..."
    
    view_count = self.count_views()
    if self.printer is not None and "default_view" in self.printer:
      default_view_name = self.printer["default_view"]
      default_view = self.get_view_named(default_view_name)
    else:
      default_view_name = default_view = None

    if view_count == 0 or default_view_name is None:
      # no views, or no default_view: just show raw type's summary
      if self.printer is not None and "summary" in self.printer:
        return _get_summary(self.printer["summary"], self.val, max_len)
    else:
      # one default view: show that view's summary if any
      if default_view is None:
        return f'<default_view "{default_view_name}" not defined>'
      if "summary" in default_view:
        return _get_summary(default_view["summary"], self.val, max_len)

    # view has no summary, or no views/printer: show nothing
    return ""

  def count_views(self):
    if self.printer is not None and "views" in self.printer:
      log(f"count_views for {self.val.type} = {len(self.printer["views"])}")
      return len(self.printer["views"])
    return 0

  def view_name(self, index : int):
    if self.printer is not None and "views" in self.printer and index < len(self.printer["views"]):
      return self.printer["views"][index]["name"]
    return f"View {index}"

  def children(self):
    log(f"DefaultPrinter children for {self.val.type}\n{self.printer}")

    view_count = self.count_views()
    if self.printer is not None and "default_view" in self.printer:
      default_view_name = self.printer["default_view"]
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
        # default view is misnamed
        # IT'S PRETTY DAMN ANNOYING THAT I CAN'T GET A NODE WITH NO DROPDOWN OR
        # AN `= ...` AFTER IT.

        # No `= ...`, but still dropdown
        # yield f'<default_view "{default_view_name}" not defined>', ""

        # No `= ...`, but still dropdown and shows base class items without
        # values?  Weird.  I guess UB.
        # yield f'<default_view "{default_view_name}" not defined>', None
        # yield f'<default_view "{default_view_name}" not defined>', (0,)

        # Still dropdown
        # yield ">ERROR<", \
        #   make_enums_tag(self.val, _MSG_ENUM(f'default_view "{default_view_name}" not defined'))

        # Still dropdown.  But since implemented with python string,
        # ArrayPrinter at least limits the depth to be only one more level, but
        # still reports:
        #   Python Exception <class 'TypeError'>: Could not convert Python object: None.
        # yield ">ERROR<", \
        #   f'default_view "{default_view_name}" not defined'

        # NOTE: Might be able to create my own gdb.Type which might have no
        #       dropdown.  Might be able to use those types instead of these
        #       numeric enums typed as a way to make views.
        #       Need to mull that over for a bit.
        #
        #       NOPE! Bastards marked gdb.Type as final!
        pass
      else:
        log(f"top-level view for {self.val.type} = {default_view_name}")
        yield from ViewPrinter(self.val, default_view).children()

    # Show static/raw/views views
    if has_static(self.val):
      log(f"has_static for {self.val.type}")
      yield "<Static>", make_enums_tag(self.val, _STATIC_ENUM)

    if view_count > 0:
      if default_view_name is not None:
        # show <Raw> tag if there are views and a default_view
        yield "<Raw>", make_enums_tag(self.val, _RAW_ENUM)

      # show views other than default_view
      for i in range(view_count):
        view_name = self.view_name(i)
        if view_name != default_view_name:
          yield f"<{view_name}>", make_enums_tag(self.val, _VIEW_ENUM(i))

class ArrayPrinter(gdb.ValuePrinter):
  def __init__(self, val : gdb.Value) -> None:
    self.val = val
    self.python_string = False

    t = self.val.type.strip_typedefs().unqualified()
    self.low, self.high = self.val.type.range()
    self.n = self.high - self.low + 1
    if t.target().unqualified() in CHAR_TYPES:
      self.summary = self.val.lazy_string(length=self.n)  # GDB handles quotes/escaping
      if str(self.summary) == "<error: Cannot access memory at address 0x0>":
        # This is a python string so don't quote it
        self.summary = self.val.string(length=self.n)
        self.python_string = True
    elif self.n == 0:
      self.summary = ""
    else:
      self.summary = None

  def to_string(self, max_len : int = MAX_SUMMARY_LEN):
    if self.summary is None:
      self.summary = f"length = {self.n} "
      if self.n == 0:
        array = "[]"
      else:
        array = "[ " + str(self.val[self.low])
        for i in range(self.low + 1, self.high):
          if len(array) > max_len:
            array += ", ..."
            break

          array += f", {self.val[i]}"
        array += " ]"
      self.summary += array
    return self.summary

  def children(self):
    if not self.python_string:
      for i in range(self.low, self.high + 1):
        yield f"[{i}]", self.val[i]
    else:
      yield f"Parent node is a temporary python string holder", None

class StaticPrinter(gdb.ValuePrinter):
  """Handler for static members"""
  def __init__(self, val : gdb.Value):
    self.val = val

  def children(self):
    yield from emit_static_children(self.val)

  def to_string(self, _ : int = MAX_SUMMARY_LEN):
    return ""

class RawPrinter(gdb.ValuePrinter):
  """Handler for raw view of members"""
  def __init__(self, val : gdb.Value, printer = None):
    self.val = val
    self.printer = printer

  def children(self):
    yield from emit_raw_children(self.val)

  def to_string(self, max_len : int = MAX_SUMMARY_LEN):
    if self.printer is not None and "summary" in self.printer:
      return _get_summary(self.printer["summary"], self.val, max_len)
    return ""

class ViewPrinter(gdb.ValuePrinter):
  """Handler for a specific view of members"""
  def __init__(self, val : gdb.Value, view : View):
    self.val = val
    self.view = view

  def children(self):
    if "node" in self.view:
      log(f"ViewPrinter node for {self.val.type} = {self.view["node"]}")
      node = self.view["node"](self.val)
      yield from node.children()
    elif "nodes" in self.view:
      log(f"ViewPrinter raw nodes for {self.val.type}")
      nodes = self.view["nodes"]
      for i in range(len(nodes)):
        name, func = nodes[i]
        log(f"  node {name} = {func(self.val)}")
        try:
          yield name, func(self.val)
        except Exception as e:
          log(f"ViewPrinter child exception: {e}\n  {traceback.format_exc()}")
          yield name, "<error>"
      # if "elements" in self.view:
      #   log(f"ViewPrinter elements for {self.val.type}")
      #   INCOMPLETE()
      #   yield from self.view["elements"](self.val)
    return

  def to_string(self, max_len : int = MAX_SUMMARY_LEN):
    if "summary" in self.view:
      return _get_summary(self.view["summary"], self.val, max_len)
    return ""

class ChunkPrinter(gdb.ValuePrinter):
  """Handler for chunked elements"""
  def __init__(self, val : gdb.Value, offset, chunk_size):
    self.val = val
    self.offset = offset
    self.chunk_size = chunk_size

  def children(self):
    yield from emit_elements(self.val, self.offset, self.chunk_size)

  def to_string(self, _ : int = MAX_SUMMARY_LEN):
    return ""

def _get_printer(val : gdb.Value, type_str : str):
  printer = _match_printer(type_str)
  if printer is not None:
    log(f"Matched printer for type: {type_str}")
    return DefaultPrinter(val, printer)

  if val.type.code in USER_TYPE_CODES:
    log(f"DefaultPrinter for struct/union type: {type_str}")
    return DefaultPrinter(val)

  if val.type.code == gdb.TYPE_CODE_ARRAY:
    log(f"ArrayPrinter for array type: {type_str}")
    return ArrayPrinter(val)
  
  return None

def _lookup_type(val : gdb.Value):
  type_str = str(val.type.unqualified())
  log(f"type: {type_str}")

  # Match synthetic node tags by looking for pointer pattern
  if "(****)" in type_str:
    enums = extract_enums_tag(val)
    actual_val = recover_value(val)
    actual_val_type_str = str(actual_val.type.unqualified())
    printer = _match_printer(actual_val_type_str)

    log(f"enums: {enums}")
    if enums == _STATIC_ENUM:
      log(f"StaticPrinter for {actual_val.type}")
      return StaticPrinter(actual_val)
    elif enums == _RAW_ENUM:
      log(f"RawPrinter for {actual_val.type}")
      return RawPrinter(actual_val, printer)
    elif enums[0] == _CHUNK_ENUM_I:
      log(f"ChunkPrinter for {actual_val.type} chunk size {enums[1]}")
      return ChunkPrinter(actual_val, offset=enums[1], chunk_size=enums[2])
    elif enums[0] == _MSG_ENUM_I:
      return MessagePrinter(actual_val, "".join(map(chr, enums[1:])))
    else:
      # everything else is a view
      view_index = enums[0] - _VIEW_ENUM_I
      log(f"ViewPrinter index {view_index} for {actual_val.type}")
      assert printer is not None, f"Type {actual_val_type_str} has no printer."
      return ViewPrinter(actual_val, printer["views"][view_index])

  printerObj = _get_printer(val, type_str)
  if printerObj:
    return printerObj

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

log("Enabling custom pretty-printers")
gdb.pretty_printers.append(_lookup_type)

# This won't be pretty printed because it is handled by another printer.
#
# Uncomment call to disable_all_printers() above to enable.
# add_printer("std::vector<.*>", {
#   "summary": lambda v: "std::vector", #f"std::vector(size={get_member_value(v, "size", int)} capacity={get_member_value(v, "capacity", int)})",
#   "views": [
#     {
#       "name": "Elements",
#       "elements": lambda v: emit_chunked_elements(get_c_range_and_size(v.cast(gdb.lookup_type("std::_Vector_base<int, std::allocator<int> >")), "_M_start", "_M_finish"), chunk_size=16)
#     },
#   ]
# })
