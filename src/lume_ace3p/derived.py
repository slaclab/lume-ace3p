"""``derived_parameters`` — quantities computed from the extracted outputs.

``output_parameters`` names what to *extract*; this block names what to *compute*
from it, so an optimization can target "distance of the fundamental from 1.3 GHz"
and a sweep table can carry arithmetic over its columns::

    derived_parameters :
      'f_target' : 1.3e9                              # a constant
      'f_error'  : 'abs(mode_freq - f_target)'        # an expression over outputs
      'RoQ_norm' : '`R/Q` / 100'                      # backticks quote a name
      'balance'  : {python: 'power_balance:balance'}  # module:callable

Entries are evaluated in declaration order inside
:meth:`~lume_ace3p.workflow_graph.Workflow.evaluate`, after extraction, so a
derived value is an output like any other: a table column, a VOCS objective or
constraint, a manifest entry, a resume comparison.

Three properties it holds to:

* **Never ``eval``.** An expression is parsed with :mod:`ast` and *interpreted*
  by a walker over a closed set of nodes (:data:`FUNCTIONS` is the whole call
  table). Anything else — attribute access, subscripts, lambdas, any other call —
  is rejected when the workflow is built, naming the node.
* **Every name is checked at build time.** A misspelling or a forward reference
  fails before the first mesh is built, not at the end of the first point.
* **The ``python:`` hatch is a pure function of the outputs.** Its signature is
  ``(outputs: dict) -> value`` and it is handed nothing else, so the source text
  of the callable is a sufficient identity to hash. That text is read from the
  module's file with :mod:`ast`, without importing it; the import happens on the
  first :meth:`DerivedSpec.compute`, which is why ``--status`` never runs it.
"""

import ast
import importlib.machinery
import importlib.util
import os
import re
import sys

import numpy as np


def _reduce_or_pair(reduce, pair):
    """``min(x)`` reduces an array; ``min(a, b)`` is elementwise."""
    def call(*args):
        if len(args) == 1:
            return reduce(args[0])
        return pair(*args)
    return call


# The whole call table, as ``name: (callable, allowed argument counts)``. A
# function enters it only once the ``python:`` hatch has absorbed the same need
# twice — the grammar is kept small on purpose.
FUNCTIONS = {
    'abs': (np.abs, {1}),
    'sqrt': (np.sqrt, {1}),
    'exp': (np.exp, {1}),
    'log': (np.log, {1}),
    'log10': (np.log10, {1}),
    'min': (_reduce_or_pair(np.min, np.minimum), {1, 2}),
    'max': (_reduce_or_pair(np.max, np.maximum), {1, 2}),
    'sum': (np.sum, {1}),
    'mean': (np.mean, {1}),
    'where': (np.where, {3}),
    'clip': (np.clip, {3}),
}

# Names an expression may use without declaring them. An output or derived name
# of the same spelling wins.
CONSTANTS = {'nan': np.nan}

_BINARY = {
    ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply,
    ast.Div: np.true_divide, ast.Pow: np.power, ast.Mod: np.mod,
}
_UNARY = {ast.USub: np.negative, ast.UAdd: np.positive, ast.Not: np.logical_not}
_COMPARE = {
    ast.Eq: np.equal, ast.NotEq: np.not_equal, ast.Lt: np.less,
    ast.LtE: np.less_equal, ast.Gt: np.greater, ast.GtE: np.greater_equal,
}
_BOOL = {ast.And: np.logical_and, ast.Or: np.logical_or}

# How a rejected node is described, for the ones a user is likely to try.
_DESCRIBED = {
    ast.Attribute: 'attribute access (a.b)',
    ast.Subscript: 'subscripts (a[i])',
    ast.Lambda: 'lambda',
    ast.NamedExpr: 'assignment (:=)',
    ast.IfExp: "conditional expressions (use where(cond, a, b))",
    ast.List: 'list literals', ast.Tuple: 'tuple literals',
    ast.Dict: 'dict literals', ast.Set: 'set literals',
    ast.ListComp: 'comprehensions', ast.GeneratorExp: 'comprehensions',
    ast.DictComp: 'comprehensions', ast.SetComp: 'comprehensions',
    ast.JoinedStr: 'f-strings',
}

_BACKTICK = re.compile(r'`([^`]*)`')
_PLACEHOLDER = '__bt{}__'


class DerivedParameterError(ValueError):
    """A ``derived_parameters`` entry that cannot be parsed, resolved or
    evaluated. A :class:`ValueError`, so the command line reports it as a
    configuration error rather than a traceback."""


# --------------------------------------------------------------------------- #
# Expressions
# --------------------------------------------------------------------------- #


class Expr:
    """One parsed, whitelisted expression.

    ``names`` is the set of free names it reads (backtick-quoted ones under their
    real spelling), excluding the function names and :data:`CONSTANTS`."""

    def __init__(self, text, tree, quoted):
        self.text = text
        self._tree = tree
        self._quoted = quoted            # placeholder -> real name
        self.names = frozenset(
            self._real(node.id) for node in ast.walk(tree)
            if isinstance(node, ast.Name) and not _is_call_target(tree, node)
            and self._real(node.id) not in CONSTANTS)

    def _real(self, name):
        return self._quoted.get(name, name)

    def evaluate(self, namespace):
        """The value of the expression over ``namespace`` (``{name: value}``).

        Elementwise over arrays, numpy broadcasting rules. Floating-point
        warnings are silenced: a dry run or an unavailable quantity is ``NaN``,
        and ``NaN`` propagating through the arithmetic is the expected outcome,
        not a warning worth printing on every point."""
        with np.errstate(all='ignore'):
            return _as_output(self._eval(self._tree.body, namespace))

    def _eval(self, node, namespace):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            name = self._real(node.id)
            if name in namespace:
                return _as_array(namespace[name], name)
            return CONSTANTS[name]
        if isinstance(node, ast.BinOp):
            return _BINARY[type(node.op)](self._eval(node.left, namespace),
                                          self._eval(node.right, namespace))
        if isinstance(node, ast.UnaryOp):
            return _UNARY[type(node.op)](self._eval(node.operand, namespace))
        if isinstance(node, ast.BoolOp):
            values = [self._eval(value, namespace) for value in node.values]
            result = values[0]
            for value in values[1:]:
                result = _BOOL[type(node.op)](result, value)
            return result
        if isinstance(node, ast.Compare):
            # a < b < c is (a < b) and (b < c), elementwise.
            left = self._eval(node.left, namespace)
            result = True
            for op, right_node in zip(node.ops, node.comparators):
                right = self._eval(right_node, namespace)
                result = np.logical_and(result, _COMPARE[type(op)](left, right))
                left = right
            return result
        if isinstance(node, ast.Call):
            function, _arity = FUNCTIONS[node.func.id]
            return function(*(self._eval(arg, namespace) for arg in node.args))
        # Unreachable: parse_expression admitted only the nodes above.
        raise DerivedParameterError(f'cannot evaluate {ast.dump(node)}')


def parse_expression(text):
    """Parse ``text`` into an :class:`Expr`, rejecting any node outside the
    grammar with a message naming it.

    Backtick-quoted names (```R/Q```, ```S(0,0)```, the pandas ``query``
    convention) are rewritten to placeholders before parsing, so a name that is
    not a Python identifier can still be read."""
    text = str(text)
    if text.count('`') % 2:
        raise DerivedParameterError(
            f"expression {text!r} has an unclosed backtick.")
    quoted = {}

    def substitute(match):
        name = match.group(1)
        if not name:
            raise DerivedParameterError(
                f"expression {text!r} has an empty backtick-quoted name.")
        placeholder = _PLACEHOLDER.format(len(quoted))
        quoted[placeholder] = name
        return f' {placeholder} '

    source = _BACKTICK.sub(substitute, text).strip()
    try:
        tree = ast.parse(source, mode='eval')
    except SyntaxError as exc:
        raise DerivedParameterError(
            f"expression {text!r} is not valid: {exc.msg}. A name that is not "
            "a Python identifier must be quoted in backticks, e.g. `R/Q`."
        ) from None
    for node in ast.walk(tree):
        _check_node(node, tree, text, quoted)
    return Expr(text, tree, quoted)


def _check_node(node, tree, text, quoted):
    """Raise unless ``node`` is in the grammar."""
    if isinstance(node, (ast.Expression, ast.Load)):
        return
    allowed_ops = (tuple(_BINARY) + tuple(_UNARY) + tuple(_COMPARE)
                   + tuple(_BOOL))
    if isinstance(node, allowed_ops):
        return
    if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare)):
        # The operator node is checked on its own; an unsupported one (<<, @,
        # in, is) fails there.
        return
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value,
                                                          (int, float)):
            raise DerivedParameterError(
                f"expression {text!r}: only numeric literals are allowed, got "
                f"{node.value!r}.")
        return
    if isinstance(node, ast.Name):
        if node.id.startswith('__') and node.id not in quoted:
            raise DerivedParameterError(
                f"expression {text!r}: names beginning with '__' are reserved; "
                "quote it in backticks if it is an output name.")
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id in quoted:
            raise DerivedParameterError(
                f"expression {text!r}: only calls to {sorted(FUNCTIONS)} are "
                "allowed.")
        name = node.func.id
        if name not in FUNCTIONS:
            raise DerivedParameterError(
                f"expression {text!r}: '{name}' is not an allowed function. "
                f"Allowed: {', '.join(sorted(FUNCTIONS))}. For anything else, "
                "use {python: 'module:callable'}.")
        if node.keywords or any(isinstance(arg, ast.Starred)
                                for arg in node.args):
            raise DerivedParameterError(
                f"expression {text!r}: '{name}' takes positional arguments "
                "only.")
        arity = FUNCTIONS[name][1]
        if len(node.args) not in arity:
            raise DerivedParameterError(
                f"expression {text!r}: '{name}' takes "
                f"{' or '.join(str(n) for n in sorted(arity))} argument(s), "
                f"got {len(node.args)}.")
        return
    what = _DESCRIBED.get(type(node), type(node).__name__)
    raise DerivedParameterError(
        f"expression {text!r} uses {what}, which derived_parameters does not "
        "allow.")


def _is_call_target(tree, name_node):
    """Whether ``name_node`` is the function of a call (``abs`` in ``abs(x)``)."""
    return any(isinstance(node, ast.Call) and node.func is name_node
               for node in ast.walk(tree))


def _as_array(value, name):
    """An output value as something numpy arithmetic accepts."""
    if isinstance(value, (list, tuple)):
        value = np.asarray(value)
    if isinstance(value, np.ndarray) and value.dtype == object:
        raise DerivedParameterError(
            f"output '{name}' is not numeric, so it cannot be used in an "
            "expression.")
    if value is None or isinstance(value, str):
        raise DerivedParameterError(
            f"output '{name}' is {value!r}, not a number.")
    return value


def _as_output(value):
    """A computed value as an output: a 0-d result is a plain float, an array
    is a float array, so tables and VOCS see numbers rather than numpy bools."""
    array = np.asarray(value)
    if array.dtype == bool or np.issubdtype(array.dtype, np.integer):
        array = array.astype(float)
    if array.ndim == 0:
        return float(array)
    return array


# --------------------------------------------------------------------------- #
# The python: hatch
# --------------------------------------------------------------------------- #


class _Callable:
    """A ``{python: 'module:callable'}`` entry: resolved and its source read at
    build time, imported on first use."""

    def __init__(self, target, base_dir):
        self.target = target
        module_name, sep, attr = str(target).partition(':')
        if not sep or not module_name or not attr:
            raise DerivedParameterError(
                f"python: target {target!r} must be 'module:callable'.")
        self.module_name, self.attr = module_name, attr
        self.base_dir = os.path.abspath(base_dir or os.getcwd())
        self.spec = _find_module(module_name, self.base_dir)
        if self.spec is None or not self.spec.origin \
                or not os.path.isfile(self.spec.origin):
            raise DerivedParameterError(
                f"python: target {target!r}: no module '{module_name}' on "
                f"sys.path or in {self.base_dir}.")
        self.source = _callable_source(self.spec.origin, attr, target)
        self._function = None

    def __call__(self, outputs):
        if self._function is None:
            self._function = self._load()
        return self._function(outputs)

    def _load(self):
        # Executed from the file the source was hashed from, and not registered
        # in sys.modules, so a second workflow over an edited file runs the edit
        # rather than a cached copy. The config's directory is on the path while
        # it runs, so a helper module beside it imports the way it would from a
        # script there.
        module = importlib.util.module_from_spec(self.spec)
        sys.path.insert(0, self.base_dir)
        try:
            self.spec.loader.exec_module(module)
        finally:
            sys.path.remove(self.base_dir)
        function = getattr(module, self.attr, None)
        if not callable(function):
            raise DerivedParameterError(
                f"python: target {self.target!r}: '{self.attr}' is not a "
                "callable.")
        return function


def _find_module(dotted, base_dir):
    """The import spec of ``dotted`` on ``[base_dir] + sys.path``, found
    **without executing** it or any parent package."""
    spec = None
    search = [base_dir] + sys.path
    for part in dotted.split('.'):
        spec = importlib.machinery.PathFinder.find_spec(part, search)
        if spec is None:
            return None
        search = list(spec.submodule_search_locations or [])
    return spec


def _callable_source(path, attr, target):
    """The source text of top-level ``attr`` in the file at ``path`` — the
    callable's identity for the hash. Read with :mod:`ast` so nothing executes.
    A name bound some other way (an assignment, an import) is hashed as the whole
    file, which over-invalidates rather than missing an edit; whether it exists
    is then only known on first use."""
    with open(path) as file:
        text = file.read()
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise DerivedParameterError(
            f"python: target {target!r}: {path} does not parse: {exc}.") from None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.name == attr:
            return ast.get_source_segment(text, node)
    if not re.search(r'\b' + re.escape(attr) + r'\b', text):
        raise DerivedParameterError(
            f"python: target {target!r}: {path} defines no '{attr}'.")
    return text


# --------------------------------------------------------------------------- #
# The block
# --------------------------------------------------------------------------- #


class DerivedSpec:
    """A resolved ``derived_parameters`` block: ``(name, kind, value)`` entries
    in declaration order, where ``kind`` is ``'constant'``, ``'expression'`` or
    ``'python'``."""

    def __init__(self, entries):
        self.entries = list(entries)

    @property
    def names(self):
        return [name for name, _kind, _value in self.entries]

    @classmethod
    def from_config(cls, block, output_names=(), base_dir=None):
        """Resolve a ``derived_parameters`` mapping against the declared
        ``output_names``. ``base_dir`` is where a ``python:`` module is looked
        for besides ``sys.path`` (the config file's directory; the working
        directory by default).

        Raises :class:`DerivedParameterError` for a malformed entry, a name that
        collides with an ``output_parameters`` name, and a name an expression
        reads that is neither an output nor an *earlier* derived entry."""
        if not isinstance(block, dict):
            raise DerivedParameterError(
                "derived_parameters must be a mapping of name: constant | "
                f"'expression' | {{python: 'module:callable'}}; got {block!r}.")
        outputs = [str(name) for name in output_names]
        known = list(outputs)
        entries = []
        for name, value in block.items():
            name = str(name)
            if name in outputs:
                raise DerivedParameterError(
                    f"derived_parameters '{name}' is also an output_parameters "
                    "name; give one of them a different name.")
            entries.append((name, *_resolve_entry(name, value, known, base_dir)))
            known.append(name)
        return cls(entries)

    def compute(self, outputs):
        """``{derived name: value}`` over the extracted ``outputs``, in
        declaration order, each entry seeing the outputs and every earlier
        derived value."""
        namespace = dict(outputs)
        derived = {}
        for name, kind, value in self.entries:
            if kind == 'constant':
                result = value
            elif kind == 'expression':
                try:
                    result = value.evaluate(namespace)
                except DerivedParameterError:
                    raise
                except Exception as exc:
                    raise DerivedParameterError(
                        f"derived_parameters '{name}' ({value.text!r}) failed: "
                        f"{type(exc).__name__}: {exc}") from exc
            else:
                result = value(dict(namespace))
                if isinstance(result, (int, float, np.number, np.ndarray,
                                       list, tuple)):
                    result = _as_output(result)
            namespace[name] = derived[name] = result
        return derived

    def canonical(self):
        """The block as :func:`lume_ace3p.state.config_hash` covers it: the
        constants, the expression texts and each ``python:`` target with its
        source text, in declaration order."""
        rendered = []
        for name, kind, value in self.entries:
            if kind == 'constant':
                rendered.append([name, kind, value])
            elif kind == 'expression':
                rendered.append([name, kind, value.text])
            else:
                rendered.append([name, kind,
                                 {'target': value.target,
                                  'source': value.source}])
        return rendered


def _resolve_entry(name, value, known, base_dir):
    """``(kind, value)`` for one entry, with every name it reads checked."""
    if isinstance(value, bool):
        raise DerivedParameterError(
            f"derived_parameters '{name}' is {value!r}; a constant must be a "
            "number.")
    if isinstance(value, (int, float)):
        return 'constant', float(value)
    if isinstance(value, str):
        try:
            expr = parse_expression(value)
        except DerivedParameterError as exc:
            raise DerivedParameterError(
                f"derived_parameters '{name}': {exc}") from None
        unknown = sorted(expr.names - set(known))
        if unknown:
            hint = ''
            if any(not other.isidentifier() for other in known):
                hint = (' A name that is not a Python identifier must be quoted '
                        'in backticks, e.g. `R/Q`.')
            raise DerivedParameterError(
                f"derived_parameters '{name}' ({value!r}) reads {unknown}, "
                "which is not an output_parameters name or an earlier "
                f"derived_parameters entry. Known here: {known}.{hint}")
        return 'expression', expr
    if isinstance(value, dict) and set(value) == {'python'}:
        try:
            return 'python', _Callable(value['python'], base_dir)
        except DerivedParameterError as exc:
            raise DerivedParameterError(
                f"derived_parameters '{name}': {exc}") from None
    raise DerivedParameterError(
        f"derived_parameters '{name}' must be a number, an expression string or "
        f"{{python: 'module:callable'}}; got {value!r}.")
