
"""
    codegen
    ~~~~~~~

    Extension to ast that allow ast -> python code generation.

    :copyright: Copyright 2008 by Armin Ronacher.
    :license: BSD.
"""
"""
    Variables definition in C
    -------------------------
    Defining variables within the Translate function is a bit of a guess work,
    using following rules.
    *   By default, a variable is a 'double'.
    *   Variable in a for loop is an int.
    *   Variable that is references with brackets is an array of doubles. The
        variable within the brackets is integer. For example, in the
        reference 'var1[var2]', var1 is a double array, and var2 is an integer.
    *   Assignment to an argument makes that argument an array, and the index in
        that assignment is 0.
        For example, the following python code
            def func(arg1, arg2):
                arg2 = 17.
        is translated to the following C code
            double func(double arg1)
            {
                arg2[0] = 17.0;
            }
        For example, the following python code is translated to the following C code
            def func(arg1, arg2):          double func(double arg1) {
                arg2 = 17.                      arg2[0] = 17.0;
                                            }
    *   All functions are defined as double, even if there is no return statement.


Update Notes
============
11/22 14:15, O.E.   Each 'visit_*' method is to build a C statement string. It
                    shold insert 4 blanks per indentation level.
                    The 'body' method will combine all the strings, by adding
                    the 'current_statement' to the c_proc string list
   11/2017, OE: variables, argument definition implemented.
   Note: An argument is considered an array if it is the target of an
        assignment. In that case it is translated to <var>[0]
11/27/2017, OE: 'pow' basicly working
  /12/2017, OE: Multiple assignment: a1,a2,...,an=b1,b2,...bn implemented
  /12/2017, OE: Power function, including special cases of
                square(x)(pow(x,2)) and cube(x)(pow(x,3)), implemented in
                translate_power, called from visit_BinOp
12/07/2017, OE: Translation of integer division, '\\' in python, implemented
                in translate_integer_divide, called from visit_BinOp
12/07/2017, OE: C variable definition handled in 'define_C_Vars'
              : Python integer division, '//', translated to C in
                'translate_integer_divide'
12/15/2017, OE: Precedence maintained by writing opening and closing
                parenthesesm '(',')', in procedure 'visit_BinOp'.
"""
import ast
import sys
from ast import NodeVisitor

BINOP_SYMBOLS = {}
BINOP_SYMBOLS[ast.Add] = '+'
BINOP_SYMBOLS[ast.Sub] = '-'
BINOP_SYMBOLS[ast.Mult] = '*'
BINOP_SYMBOLS[ast.Div] = '/'
BINOP_SYMBOLS[ast.Mod] = '%'
BINOP_SYMBOLS[ast.Pow] = '**'
BINOP_SYMBOLS[ast.LShift] = '<<'
BINOP_SYMBOLS[ast.RShift] = '>>'
BINOP_SYMBOLS[ast.BitOr] = '|'
BINOP_SYMBOLS[ast.BitXor] = '^'
BINOP_SYMBOLS[ast.BitAnd] = '&'
BINOP_SYMBOLS[ast.FloorDiv] = '//'

BOOLOP_SYMBOLS = {}
BOOLOP_SYMBOLS[ast.And] = '&&'
BOOLOP_SYMBOLS[ast.Or]  = '||'

CMPOP_SYMBOLS = {}
CMPOP_SYMBOLS[ast.Eq]    = '=='
CMPOP_SYMBOLS[ast.NotEq] = '!='
CMPOP_SYMBOLS[ast.Lt] = '<'
CMPOP_SYMBOLS[ast.LtE] = '<='
CMPOP_SYMBOLS[ast.Gt] = '>'
CMPOP_SYMBOLS[ast.GtE] = '>='
CMPOP_SYMBOLS[ast.Is] = 'is'
CMPOP_SYMBOLS[ast.IsNot] = 'is not'
CMPOP_SYMBOLS[ast.In] = 'in'
CMPOP_SYMBOLS[ast.NotIn] = 'not in'

UNARYOP_SYMBOLS = {}
UNARYOP_SYMBOLS[ast.Invert] = '~'
UNARYOP_SYMBOLS[ast.Not] = 'not'
UNARYOP_SYMBOLS[ast.UAdd] = '+'
UNARYOP_SYMBOLS[ast.USub] = '-'


#def to_source(node, indent_with=' ' * 4, add_line_information=False):
def to_source(node, func_name):
    """This function can convert a node tree back into python sourcecode.
    This is useful for debugging purposes, especially if you're dealing with
    custom asts not generated by python itself.

    It could be that the sourcecode is evaluable when the AST itself is not
    compilable / evaluable.  The reason for this is that the AST contains some
    more data than regular sourcecode does, which is dropped during
    conversion.

    Each level of indentation is replaced with `indent_with`.  Per default this
    parameter is equal to four spaces as suggested by PEP 8, but it might be
    adjusted to match the application's styleguide.

    If `add_line_information` is set to `True` comments for the line numbers
    of the nodes are added to the output.  This can be used to spot wrong line
    number information of statement nodes.
    """
    generator = SourceGenerator(' ' * 4, False)
#    generator.required_functions = func_name
    generator.visit(node)

#    return ''.join(generator.result)
    return ''.join(generator.c_proc)

def isevaluable(s):
    try:
        eval(s)
        return True
    except:
        return False

class SourceGenerator(NodeVisitor):
    """This visitor is able to transform a well formed syntax tree into python
    sourcecode.  For more details have a look at the docstring of the
    `node_to_source` function.
    """

    def __init__(self, indent_with, add_line_information=False):
        self.result = []
        self.indent_with = indent_with
        self.add_line_information = add_line_information
        self.indentation = 0
        self.new_lines = 0
        self.c_proc = []
# for C
        self.signature_line = 0
        self.arguments = []
        self.name = ""
        self.warnings = []
        self.Statements = []
        self.current_statement = ""
        self.strMethodSignature = ""
        self.C_Vars = []
        self.C_IntVars = []
        self.MathIncludeed = False
        self.C_Pointers = []
        self.C_DclPointers = []
        self.C_Functions = []
        self.C_Vectors = []
        self.SubRef = False
        self.InSubscript = False
        self.Tuples = []
        self.required_functions = []
        self.is_sequence = False
        self.visited_args = False

    def write_python(self, x):
        if self.new_lines:
            if self.result:
                self.result.append('\n' * self.new_lines)
            self.result.append(self.indent_with * self.indentation)
            self.new_lines = 0
        self.result.append(x)

    def write_c(self, x):
        self.current_statement += x

    def add_c_line(self, x):
        string = ''
        for i in range(self.indentation):
            string += ("    ")
        string += str(x)
        self.c_proc.append(str(string + "\n"))
        x = ''

    def add_current_line(self):
        if(len(self.current_statement) > 0):
            self.add_c_line(self.current_statement)
            self.current_statement = ''

    def AddUniqueVar(self, new_var):
        if((new_var not in self.C_Vars)):
            self.C_Vars.append(str(new_var))

    def WriteSincos(self, node):
        angle = str(node.args[0].id)
        self.write_c(node.args[1].id + " = sin(" + angle + ");")
        self.add_current_line()
        self.write_c(node.args[2].id + " = cos(" + angle + ");")
        self.add_current_line()
        for arg in node.args:
            self.AddUniqueVar(arg.id)

    def newline(self, node=None, extra=0):
        self.new_lines = max(self.new_lines, 1 + extra)
        if node is not None and self.add_line_information:
            self.write_c('# line: %s' % node.lineno)
            self.new_lines = 1
        if(len(self.current_statement)):
            self.Statements.append(self.current_statement)
            self.current_statement = ''

    def body(self, statements):
        if(len(self.current_statement)):
            self.add_current_line()
        self.new_line = True
        self.indentation += 1
        for stmt in statements:
            target_name = ''
            if(hasattr(stmt, 'targets')):
                if(hasattr(stmt.targets[0], 'id')):
                    target_name = stmt.targets[0].id # target name needed for debug only
            self.visit(stmt)
        self.add_current_line() # just for breaking point. to be deleted.
        self.indentation -= 1

    def body_or_else(self, node):
        self.body(node.body)
        if node.orelse:
            self.newline()
            self.write_c('else:')
            self.body(node.orelse)

    def signature(self, node):
        want_comma = []
        def write_comma():
            if want_comma:
                self.write_c(', ')
            else:
                want_comma.append(True)
# for C
        for arg in node.args:
            self.arguments.append(arg.arg)

        padding = [None] *(len(node.args) - len(node.defaults))
        for arg, default in zip(node.args, padding + node.defaults):
            if default is not None:
                self.warnings.append("Default Parameter unknown to C")
                w_str = "Default Parameters are unknown to C: '" + arg.arg + \
                        " = " + str(default.n) + "'"
                self.warnings.append(w_str)
#                self.write_python('=')
#                self.visit(default)

    def decorators(self, node):
        for decorator in node.decorator_list:
            self.newline(decorator)
            self.write_python('@')
            self.visit(decorator)

    # Statements

    def visit_Assert(self, node):
        self.newline(node)
        self.write_c('assert ')
        self.visit(node.test)
        if node.msg is not None:
            self.write_python(', ')
            self.visit(node.msg)

    def define_C_Vars(self, target):
        if(hasattr(target, 'id')):
# a variable is considered an array if it apears in the agrument list
# and being assigned to. For example, the variable p in the following
# sniplet is a pointer, while q is not
# def somefunc(p, q):
#  p = q + 1
#  return
#
            if(target.id not in self.C_Vars):
                if(target.id in self.arguments):
                    idx = self.arguments.index(target.id)
                    new_target = self.arguments[idx] + "[0]"
                    if(new_target not in self.C_Pointers):
                        target.id = new_target
                        self.C_Pointers.append(self.arguments[idx])
                else:
                    self.C_Vars.append(target.id)

    def add_semi_colon(self):
        semi_pos = self.current_statement.find(';')
        if(semi_pos > 0.0):
            self.current_statement = self.current_statement.replace(';','')
        self.write_c(';')

    def visit_Assign(self, node):
        self.add_current_line()
        for idx, target in enumerate(node.targets): # multi assign, as in 'a = b = c = 7'
            if idx:
                self.write_c(' = ')
            self.define_C_Vars(target)
            self.visit(target)
        if(len(self.Tuples) > 0):
            tplTargets = list(self.Tuples)
            self.Tuples.clear()
        self.write_c(' = ')
        self.is_sequence = False
        self.visited_args = False
        self.visit(node.value)
        self.add_semi_colon()
#        self.write_c(';')
        self.add_current_line()
        for n, item in enumerate(self.Tuples):
            self.visit(tplTargets[n])
            self.write_c(' = ')
            self.visit(item)
            self.add_semi_colon()
            self.add_current_line()
        if((self.is_sequence) and (not self.visited_args)):
            for target in node.targets:
                if(hasattr(target, 'id')):
                    if((target.id in self.C_Vars) and(target.id not in self.C_DclPointers)):
                        if(target.id not in self.C_DclPointers):
                            self.C_DclPointers.append(target.id)
                            if(target.id in self.C_Vars):
                                self.C_Vars.remove(target.id)
        self.current_statement = ''

    def visit_AugAssign(self, node):
        if(node.target.id not in self.C_Vars):
            if(node.target.id not in self.arguments):
                self.C_Vars.append(node.target.id)
        self.visit(node.target)
        self.write_c(' ' + BINOP_SYMBOLS[type(node.op)] + '= ')
        self.visit(node.value)
        self.add_semi_colon()
#        self.write_c(';')
        self.add_current_line()

    def visit_ImportFrom(self, node):
        self.newline(node)
        self.write_python('from %s%s import ' %('.' * node.level, node.module))
        for idx, item in enumerate(node.names):
            if idx:
                self.write_python(', ')
            self.write_python(item)

    def visit_Import(self, node):
        self.newline(node)
        for item in node.names:
            self.write_python('import ')
            self.visit(item)

    def visit_Expr(self, node):
        self.newline(node)
        self.generic_visit(node)

    def listToDeclare(self, Vars):
        s = ''
        if(len(Vars) > 0):
            s = ",".join(Vars)
        return(s)

    def write_C_Pointers(self, start_var):
        if(len(self.C_DclPointers) > 0):
            vars = ""
            for c_ptr in self.C_DclPointers:
                if(len(vars) > 0):
                    vars += ", "
                if(c_ptr not in self.arguments):
                    vars += "*" + c_ptr
                if(c_ptr in self.C_Vars):
                    if(c_ptr in self.C_Vars):
                        self.C_Vars.remove(c_ptr)
            if(len(vars) > 0):
                c_dcl = "    double " + vars + ";"
                self.c_proc.insert(start_var, c_dcl + "\n")
                start_var += 1
        return start_var

    def insert_C_Vars(self, start_var):
        fLine = False
        start_var = self.write_C_Pointers(start_var)
        if(len(self.C_IntVars) > 0):
            for var in self.C_IntVars:
                if(var in self.C_Vars):
                    self.C_Vars.remove(var)
            s = self.listToDeclare(self.C_IntVars)
            self.c_proc.insert(start_var, "    int " + s + ";\n")
            fLine = True
            start_var += 1

        if(len(self.C_Vars) > 0):
            s = self.listToDeclare(self.C_Vars)
            self.c_proc.insert(start_var, "    double " + s + ";\n")
            fLine = True
            start_var += 1
#        if(len(self.C_IntVars) > 0):
#            s = self.listToDeclare(self.C_IntVars)
#            self.c_proc.insert(start_var, "    int " + s + ";\n")
#            fLine = True
#            start_var += 1
        if(len(self.C_Vectors) > 0):
            s = self.listToDeclare(self.C_Vectors)
            for n in range(len(self.C_Vectors)):
                name = "vec" + str(n+1)
                c_dcl = "    double " + name + "[] = {" + self.C_Vectors[n] + "};"
                self.c_proc.insert(start_var, c_dcl + "\n")
                start_var += 1
        self.C_Vars.clear()
        self.C_IntVars.clear()
        self.C_Vectors.clear()
        self.C_Pointers.clear()
        self.C_DclPointers
        if(fLine == True):
            self.c_proc.insert(start_var, "\n")
        return
        s = ''
        for n in range(len(self.C_Vars)):
            s += str(self.C_Vars[n])
            if n < len(self.C_Vars) - 1:
                s += ", "
        if(len(s) > 0):
            self.c_proc.insert(start_var, "    double " + s + ";\n")
            self.c_proc.insert(start_var + 1, "\n")

    def writeInclude(self):
        if(self.MathIncludeed == False):
            self.add_c_line("#include <math.h>\n")
            self.add_c_line("static double pi = 3.14159265359;\n")
            self.MathIncludeed = True

    def ListToString(self, strings):
        s = ''
        for n in range(len(strings)):
            s += strings[n]
            if(n < (len(strings) - 1)):
                s += ", "
        return(s)

    def getMethodSignature(self):
#        args_str = ListToString(self.arguments)
        args_str = ''
        for n in range(len(self.arguments)):
            args_str += "double " + self.arguments[n]
            if(n < (len(self.arguments) - 1)):
                args_str += ", "
        return(args_str)
#        self.strMethodSignature = 'double ' + self.name + '(' + args_str + ")"

    def InsertSignature(self):
        args_str = ''
        for n in range(len(self.arguments)):
            args_str += "double " + self.arguments[n]
            if(self.arguments[n] in self.C_Pointers):
                args_str += "[]"
            if(n < (len(self.arguments) - 1)):
                args_str += ", "
        self.strMethodSignature = 'double ' + self.name + '(' + args_str + ")"
        if(self.signature_line >= 0):
            self.c_proc.insert(self.signature_line, self.strMethodSignature)

    def visit_FunctionDef(self, node):
        self.newline(extra=1)
        self.decorators(node)
        self.newline(node)
        self.arguments = []
        self.name = node.name
#        if self.name not in self.required_functions[0]:
#           return
        print("Parsing '" + self.name + "'")
        args_str = ""

        self.visit(node.args)
# for C
#        self.writeInclude()
        self.getMethodSignature()
# for C
        self.signature_line = len(self.c_proc)
#        self.add_c_line(self.strMethodSignature)
        self.add_c_line("\n{")
        start_vars = len(self.c_proc) + 1
        self.body(node.body)
        self.add_c_line("}\n")
        self.InsertSignature()
        self.insert_C_Vars(start_vars)
        self.C_Pointers = []

    def visit_ClassDef(self, node):
        have_args = []
        def paren_or_comma():
            if have_args:
                self.write_python(', ')
            else:
                have_args.append(True)
                self.write_python('(')

        self.newline(extra=2)
        self.decorators(node)
        self.newline(node)
        self.write_python('class %s' % node.name)
        for base in node.bases:
            paren_or_comma()
            self.visit(base)
        # XXX: the if here is used to keep this module compatible
        #      with python 2.6.
        if hasattr(node, 'keywords'):
            for keyword in node.keywords:
                paren_or_comma()
                self.write_python(keyword.arg + '=')
                self.visit(keyword.value)
            if node.starargs is not None:
                paren_or_comma()
                self.write_python('*')
                self.visit(node.starargs)
            if node.kwargs is not None:
                paren_or_comma()
                self.write_python('**')
                self.visit(node.kwargs)
        self.write_python(have_args and '):' or ':')
        self.body(node.body)

    def visit_If(self, node):
        self.write_c('if ')
        self.visit(node.test)
        self.write_c(' {')
        self.body(node.body)
        self.add_c_line('}')
        while True:
            else_ = node.orelse
            if len(else_) == 0:
                break
#            elif hasattr(else_, 'orelse'):
            elif len(else_) == 1 and isinstance(else_[0], ast.If):
                node = else_[0]
#                self.newline()
                self.write_c('else if ')
                self.visit(node.test)
                self.write_c(' {')
                self.body(node.body)
                self.add_current_line()
                self.add_c_line('}')
#                break
            else:
                self.newline()
                self.write_c('else {')
                self.body(node.body)
                self.add_c_line('}')
                break

    def getNodeLineNo(self, node):
        line_number = -1
        if(hasattr(node,'value')):
            line_number = node.value.lineno
        elif hasattr(node,'iter'):
            if hasattr(node.iter,'lineno'):
                line_number = node.iter.lineno
        return(line_number)

    def GetNodeAsString(self, node):
        res = ''
        if(hasattr(node, 'n')):
            res = str(node.n)
        elif(hasattr(node, 'id')):
            res = node.id
        return(res)

    def GetForRange(self, node):
        stop = ""
        start = '0'
        step = '1'
        for_args = []
        temp_statement = self.current_statement
        self.current_statement = ''
        for arg in node.iter.args:
            self.visit(arg)
            for_args.append(self.current_statement)
            self.current_statement = ''
        self.current_statement = temp_statement
        if(len(for_args) == 1):
            stop = for_args[0]
        elif(len(for_args) == 2):
            start = for_args[0]
            stop = for_args[1]
        elif(len(for_args) == 3):
            start = for_args[0]
            stop = for_args[1]
            start = for_args[2]
        else:
            raise("Ilegal for loop parameters")
        return(start, stop, step)

    def visit_For(self, node):
# node: for iterator is stored in node.target.
# Iterator name is in node.target.id.
        self.add_current_line()
#        if(len(self.current_statement) > 0):
#            self.add_c_line(self.current_statement)
#            self.current_statement = ''
        fForDone = False
        self.current_statement = ''
        if(hasattr(node.iter, 'func')):
            if(hasattr(node.iter.func, 'id')):
                if(node.iter.func.id == 'range'):
                    self.visit(node.target)
                    iterator = self.current_statement
                    self.current_statement = ''
                    if(iterator not in self.C_IntVars):
                        self.C_IntVars.append(iterator)
                    start, stop, step = self.GetForRange(node)
                    self.write_c("for(" + iterator + "=" + str(start) + \
                                  " ; " + iterator + " < " + str(stop) + \
                                  " ; " + iterator + " += " + str(step) + ") {")
                    self.body_or_else(node)
                    self.write_c("}")
                    fForDone = True
        if(fForDone == False):
            line_number = self.getNodeLineNo(node)
            self.current_statement = ''
            self.write_c('for ')
            self.visit(node.target)
            self.write_c(' in ')
            self.visit(node.iter)
            self.write_c(':')
            errStr = "Conversion Error in function " + self.name + ", Line #" + str(line_number)
            errStr += "\nPython for expression not supported: '" + self.current_statement + "'"
            raise Exception(errStr)

    def visit_While(self, node):
        self.newline(node)
        self.write_c('while ')
        self.visit(node.test)
        self.write_c(':')
        self.body_or_else(node)

    def visit_With(self, node):
        self.newline(node)
        self.write_python('with ')
        self.visit(node.context_expr)
        if node.optional_vars is not None:
            self.write_python(' as ')
            self.visit(node.optional_vars)
        self.write_python(':')
        self.body(node.body)

    def visit_Pass(self, node):
        self.newline(node)
        self.write_python('pass')

    def visit_Print(self, node):
# XXX: python 2.6 only
        self.newline(node)
        self.write_c('print ')
        want_comma = False
        if node.dest is not None:
            self.write_c(' >> ')
            self.visit(node.dest)
            want_comma = True
        for value in node.values:
            if want_comma:
                self.write_c(', ')
            self.visit(value)
            want_comma = True
        if not node.nl:
            self.write_c(',')

    def visit_Delete(self, node):
        self.newline(node)
        self.write_python('del ')
        for idx, target in enumerate(node):
            if idx:
                self.write_python(', ')
            self.visit(target)

    def visit_TryExcept(self, node):
        self.newline(node)
        self.write_python('try:')
        self.body(node.body)
        for handler in node.handlers:
            self.visit(handler)

    def visit_TryFinally(self, node):
        self.newline(node)
        self.write_python('try:')
        self.body(node.body)
        self.newline(node)
        self.write_python('finally:')
        self.body(node.finalbody)

    def visit_Global(self, node):
        self.newline(node)
        self.write_python('global ' + ', '.join(node.names))

    def visit_Nonlocal(self, node):
        self.newline(node)
        self.write_python('nonlocal ' + ', '.join(node.names))

    def visit_Return(self, node):
        self.newline(node)
        if node.value is None:
            self.write_c('return')
        else:
            self.write_c('return(')
            self.visit(node.value)
        self.write_c(')')
        self.add_semi_colon()
        self.add_c_line(self.current_statement)
        self.current_statement = ''

    def visit_Break(self, node):
        self.newline(node)
        self.write_c('break')

    def visit_Continue(self, node):
        self.newline(node)
        self.write_c('continue')

    def visit_Raise(self, node):
        # XXX: Python 2.6 / 3.0 compatibility
        self.newline(node)
        self.write_python('raise')
        if hasattr(node, 'exc') and node.exc is not None:
            self.write_python(' ')
            self.visit(node.exc)
            if node.cause is not None:
                self.write_python(' from ')
                self.visit(node.cause)
        elif hasattr(node, 'type') and node.type is not None:
            self.visit(node.type)
            if node.inst is not None:
                self.write_python(', ')
                self.visit(node.inst)
            if node.tback is not None:
                self.write_python(', ')
                self.visit(node.tback)

    # Expressions

    def visit_Attribute(self, node):
        errStr = "Conversion Error in function " + self.name + ", Line #" + str(node.value.lineno)
        errStr += "\nPython expression not supported: '" + node.value.id + "." + node.attr + "'"
        raise Exception(errStr)
        self.visit(node.value)
        self.write_python('.' + node.attr)

    def visit_Call(self, node):
        want_comma = []
        def write_comma():
            if want_comma:
                self.write_c(', ')
            else:
                want_comma.append(True)
        if(hasattr(node.func, 'id')):
            if(node.func.id not in self.C_Functions):
                self.C_Functions.append(node.func.id)
            if(node.func.id == 'abs'):
                self.write_c("fabs ")
            elif(node.func.id == 'int'):
                self.write_c('(int) ')
            elif(node.func.id == "SINCOS"):
                self.WriteSincos(node)
                return
            else:
                self.visit(node.func)
        else:
            self.visit(node.func)
#self.C_Functions
        self.write_c('(')
        for arg in node.args:
            write_comma()
            self.visited_args = True
            self.visit(arg)
        for keyword in node.keywords:
            write_comma()
            self.write_c(keyword.arg + '=')
            self.visit(keyword.value)
        if hasattr(node, 'starargs'):
            if node.starargs is not None:
                write_comma()
                self.write_c('*')
                self.visit(node.starargs)
        if hasattr(node, 'kwargs'):
            if node.kwargs is not None:
                write_comma()
                self.write_c('**')
                self.visit(node.kwargs)
        self.write_c(');')

    def visit_Name(self, node):
        self.write_c(node.id)
        if((node.id in self.C_Pointers) and(not self.SubRef)):
            self.write_c("[0]")
        name = ""
        sub = node.id.find("[")
        if(sub > 0):
            name = node.id[0:sub].strip()
        else:
            name = node.id
#       add variable to C_Vars if it ins't there yet, not an argument and not a number
        if((name not in self.C_Functions) and (name not in self.C_Vars) and \
            (name not in self.C_IntVars) and (name not in self.arguments) and \
            (name.isnumeric() == False)):
            if(self.InSubscript):
                self.C_IntVars.append(node.id)
            else:
                self.C_Vars.append(node.id)

    def visit_Str(self, node):
        self.write_c(repr(node.s))

    def visit_Bytes(self, node):
        self.write_c(repr(node.s))

    def visit_Num(self, node):
        self.write_c(repr(node.n))

    def visit_Tuple(self, node):
        for idx, item in enumerate(node.elts):
            if idx:
                self.Tuples.append(item)
            else:
                self.visit(item)

    def sequence_visit(left, right):
        def visit(self, node):
            self.is_sequence = True
            s = ""
            for idx, item in enumerate(node.elts):
                if((idx > 0) and(len(s) > 0)):
                    s += ', '
                if(hasattr(item, 'id')):
                    s += item.id
                elif(hasattr(item, 'n')):
                    s += str(item.n)
            if(len(s) > 0):
                self.C_Vectors.append(s)
                vec_name = "vec"  + str(len(self.C_Vectors))
                self.write_c(vec_name)
                vec_name += "#"
        return visit

    visit_List = sequence_visit('[', ']')
    visit_Set = sequence_visit('{', '}')
    del sequence_visit

    def visit_Dict(self, node):
        self.write_python('{')
        for idx, (key, value) in enumerate(zip(node.keys, node.values)):
            if idx:
                self.write_python(', ')
            self.visit(key)
            self.write_python(': ')
            self.visit(value)
        self.write_python('}')

    def get_special_power(self, string):
        function_name = ''
        is_negative_exp = False
        if(isevaluable(str(self.current_statement))):
            exponent = eval(string)
            is_negative_exp = exponent < 0
            abs_exponent = abs(exponent)
            if(abs_exponent == 2):
                function_name = "square"
            elif(abs_exponent == 3):
                function_name = "cube"
            elif(abs_exponent == 0.5):
                function_name = "sqrt"
            elif(abs_exponent == 1.0/3.0):
                function_name = "cbrt"
        if(function_name == ''):
            function_name = "pow"
        return function_name, is_negative_exp

    def translate_power(self, node):
# get exponent by visiting the right hand argument.
        function_name = "pow"
        temp_statement = self.current_statement
# 'visit' functions write the results to the 'current_statement' class memnber
# Here, a temporary variable, 'temp_statement', is used, that enables the
# use of the 'visit' function
        self.current_statement = ''
        self.visit(node.right)
        exponent = self.current_statement.replace(' ', '')
        function_name, is_negative_exp = self.get_special_power(self.current_statement)
        self.current_statement = temp_statement
        if(is_negative_exp):
            self.write_c("1.0 /(")
        self.write_c(function_name + "(")
        self.visit(node.left)
        if(function_name == "pow"):
            self.write_c(", ")
            self.visit(node.right)
        self.write_c(")")
        if(is_negative_exp):
            self.write_c(")")
        self.write_c(" ")

    def translate_integer_divide(self, node):
        self.write_c("(int)(")
        self.visit(node.left)
        self.write_c(") /(int)(")
        self.visit(node.right)
        self.write_c(")")

    def visit_BinOp(self, node):
        self.write_c("(")
        if('%s' % BINOP_SYMBOLS[type(node.op)] == BINOP_SYMBOLS[ast.Pow]):
            self.translate_power(node)
        elif('%s' % BINOP_SYMBOLS[type(node.op)] == BINOP_SYMBOLS[ast.FloorDiv]):
            self.translate_integer_divide(node)
        else:
            self.visit(node.left)
            self.write_c(' %s ' % BINOP_SYMBOLS[type(node.op)])
            self.visit(node.right)
        self.write_c(")")

#       for C
    def visit_BoolOp(self, node):
        self.write_c('(')
        for idx, value in enumerate(node.values):
            if idx:
                self.write_c(' %s ' % BOOLOP_SYMBOLS[type(node.op)])
            self.visit(value)
        self.write_c(')')

    def visit_Compare(self, node):
        self.write_c('(')
        self.visit(node.left)
        for op, right in zip(node.ops, node.comparators):
            self.write_c(' %s ' % CMPOP_SYMBOLS[type(op)])
            self.visit(right)
        self.write_c(')')

    def visit_UnaryOp(self, node):
        self.write_c('(')
        op = UNARYOP_SYMBOLS[type(node.op)]
        self.write_c(op)
        if op == 'not':
            self.write_c(' ')
        self.visit(node.operand)
        self.write_c(')')

    def visit_Subscript(self, node):
        if(node.value.id not in self.C_Pointers):
            self.C_Pointers.append(node.value.id)
        self.SubRef = True
        self.visit(node.value)
        self.SubRef = False
        self.write_c('[')
        self.InSubscript = True
        self.visit(node.slice)
        self.InSubscript = False
        self.write_c(']')

    def visit_Slice(self, node):
        if node.lower is not None:
            self.visit(node.lower)
        self.write_python(':')
        if node.upper is not None:
            self.visit(node.upper)
        if node.step is not None:
            self.write_python(':')
            if not(isinstance(node.step, Name) and node.step.id == 'None'):
                self.visit(node.step)

    def visit_ExtSlice(self, node):
        for idx, item in node.dims:
            if idx:
                self.write_python(', ')
            self.visit(item)

    def visit_Yield(self, node):
        self.write_python('yield ')
        self.visit(node.value)

    def visit_Lambda(self, node):
        self.write_python('lambda ')
        self.visit(node.args)
        self.write_python(': ')
        self.visit(node.body)

    def visit_Ellipsis(self, node):
        self.write_python('Ellipsis')

    def generator_visit(left, right):
        def visit(self, node):
            self.write_python(left)
            self.write_c(left)
            self.visit(node.elt)
            for comprehension in node.generators:
                self.visit(comprehension)
            self.write_c(right)
#            self.write_python(right)
        return visit

    visit_ListComp = generator_visit('[', ']')
    visit_GeneratorExp = generator_visit('(', ')')
    visit_SetComp = generator_visit('{', '}')
    del generator_visit

    def visit_DictComp(self, node):
        self.write_python('{')
        self.visit(node.key)
        self.write_python(': ')
        self.visit(node.value)
        for comprehension in node.generators:
            self.visit(comprehension)
        self.write_python('}')

    def visit_IfExp(self, node):
        self.visit(node.body)
        self.write_c(' if ')
        self.visit(node.test)
        self.write_c(' else ')
        self.visit(node.orelse)

    def visit_Starred(self, node):
        self.write_c('*')
        self.visit(node.value)

    def visit_Repr(self, node):
        # XXX: python 2.6 only
        self.write_c('`')
        self.visit(node.value)
        self.write_python('`')

    # Helper Nodes

    def visit_alias(self, node):
        self.write_python(node.name)
        if node.asname is not None:
            self.write_python(' as ' + node.asname)

    def visit_comprehension(self, node):
        self.write_c(' for ')
        self.visit(node.target)
        self.write_C(' in ')
#        self.write_python(' in ')
        self.visit(node.iter)
        if node.ifs:
            for if_ in node.ifs:
                self.write_python(' if ')
                self.visit(if_)

#    def visit_excepthandler(self, node):
#        self.newline(node)
#        self.write_python('except')
#        if node.type is not None:
#            self.write_python(' ')
#            self.visit(node.type)
#            if node.name is not None:
#                self.write_python(' as ')
#                self.visit(node.name)
#        self.body(node.body)

    def visit_arguments(self, node):
        self.signature(node)

def Iq1(q, porod_scale, porod_exp, lorentz_scale, lorentz_length, peak_pos, lorentz_exp=17):
    z1 = z2 = z = abs(q - peak_pos) * lorentz_length
    if(q > p):
        q = p + 17
        p = q - 5
    z3 = -8
    inten = (porod_scale / q ** porod_exp
                + lorentz_scale /(1 + z ** lorentz_exp))
    return inten

def Iq(q, porod_scale, porod_exp, lorentz_scale, lorentz_length, peak_pos, lorentz_exp=17):
    z1 = z2 = z = abs(q - peak_pos) * lorentz_length
    if(q > p):
        q = p + 17
        p = q - 5
    elif(q == p):
        q = p * q
        q *= z1
        p = z1
    elif(q == 17):
        q = p * q - 17
    else:
        q += 7
    z3 = -8
    inten = (porod_scale / q ** porod_exp
                + lorentz_scale /(1 + z ** lorentz_exp))
    return inten

def print_function(f=None):
    """
    Print out the code for the function
    """
    # Include some comments to see if they get printed
    import ast
    import inspect
    if f is not None:
        tree = ast.parse(inspect.getsource(f))
        tree_source = to_source(tree)
        print(tree_source)

def translate(functions, constants=0):
    sniplets = []
    sniplets.append("#include <math.h>")
    sniplets.append("static double pi = 3.14159265359;")
    for source,fname,line_no in functions:
        line_directive = '#line %d "%s"' %(line_no,fname)
        line_directive = line_directive.replace('\\','\\\\')
#        sniplets.append(line_directive)
        tree = ast.parse(source)
        sniplet = to_source(tree, functions) # in the future add filename, offset, constants
        sniplets.append(sniplet)
    c_code = "\n".join(sniplets)
    f_out = open ("xlate.c", "w+")
    f_out.write (c_code)
    f_out.close()
    return("\n".join(sniplets))

def get_file_names():
    fname_in = ""
    fname_out = ""
    if(len(sys.argv) > 1):
        fname_in = sys.argv[1]
        fname_base = os.path.splitext(fname_in)
        if(len(sys.argv) == 2):
            fname_out = str(fname_base[0]) + '.c'
        else:
            fname_out = sys.argv[2]
        if(len(fname_in) > 0):
            python_file = open(sys.argv[1], "r")
            if(len(fname_out) > 0):
                file_out = open(fname_out, "w+")
    return len(sys.argv), fname_in, fname_out

if __name__ == "__main__":
    import os
    print("Parsing...using Python" + sys.version)
    try:
        fname_in = ""
        fname_out = ""
        if(len(sys.argv) == 1):
            print("Usage:\npython parse01.py <infile> [<outfile>](if omitted, output file is '<infile>.c'")
        else:
            fname_in = sys.argv[1]
            fname_base = os.path.splitext(fname_in)
            if(len(sys.argv) == 2):
                fname_out = str(fname_base[0]) + '.c'
            else:
                fname_out = sys.argv[2]
            if(len(fname_in) > 0):
                python_file = open(sys.argv[1], "r")
                if(len(fname_out) > 0):
                    file_out = open(fname_out, "w+")
                functions = ["MultAsgn", "Iq41", "Iq2"]
                tpls = [functions, fname_in, 0]
                c_txt = translate(tpls)
                file_out.write(c_txt)
                file_out.close()
    except Exception as excp:
        print("Error:\n" + str(excp.args))
    print("...Done")
