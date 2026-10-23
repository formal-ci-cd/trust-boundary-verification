# ${{ ... }}やifの式を構文木へ変換し，有限の「値候補と成立条件」の組へ下げる．
# 同じcontext参照は同じ値を共有する．
# 参照のたびに別の未知Booleanを作る構成にはしない．
# 式の評価結果を求める処理であり，workflow全体の危険到達性を判定する処理ではない．
"""GitHub Actionsの限定式を，構文木と有限値の成立条件へ変換する．

parseの入力はifや${{ ... }}の式，出力はNodeの構文木である．
evaluateは具体的な候補値1組を評価し，lowerは共有参照をまとめて候補の直積を調べる．
出力の各要素は「値，その値になるSMV条件」の組であり，危険到達性そのものではない．
compilerがcontextを解決し，同じ参照に同じ状態変数を渡すことで相関を保つ．
型変換や文字列比較はこのfileの限定規則を用い，未知関数・式の上限超過は例外にする．
"""
from __future__ import annotations
import itertools
import json
import math
import re
from dataclasses import dataclass

class UnsupportedExpression(ValueError):
    """対応外の式・関数・有限domainを通知する例外．"""
    pass

@dataclass(frozen=True)
class Node:
    """式の種類・値・子nodeを持つimmutableな構文木．"""
    # nodeの種類をliteral/ref/call/演算子等で表し，後段の分岐で評価方法を選ぶ．
    kind: str
    value: object = None
    # 子nodeをtupleで保持し，frozen=Trueのdataclassとして構文木を変更せず受け渡す．
    args: tuple = ()
TOKEN = re.compile(r"\s+|'(?:''|[^'])*'|(?:0[xX][0-9a-fA-F]+|-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)|[A-Za-z_][\w.-]*|==|!=|<=|>=|&&|\|\||[!<>()\[\],.]")

# 文字列をtokenへ分割し，演算子の優先順位を保った構文木を作る．
def parse(source):
    """限定式をtokenに分け，優先順位付きのASTへ変換する．"""
    # YAML側ですでにBooleanや数値になっていれば，文字列の解析をせずliteral nodeにする．
    if not isinstance(source, str):
        return Node('literal', source)
    # 式の外側の空白を除き，tokenの照合を始める．
    text = source.strip()
    # ${{ ... }}で囲まれた式は外側の区切りを外し，内部だけを読む．
    if text.startswith('${{') and text.endswith('}}'):
        text = text[3:-2].strip()
    # 読んだtokenの列と，直前に読み終えた位置を別々に初期化する．
    tokens, end = ([], 0)
    # 定義済みtokenの正規表現を式全体へ順に適用し，未知の文字を飛ばしていないかも検査する．
    for match in TOKEN.finditer(text):
        # 前のtokenとの間に認識できない部分があれば，その位置から先をエラーにする．
        if match.start() != end:
            raise UnsupportedExpression('unrecognized expression token at ' + text[end:])
        # 今回一致した範囲の末尾を覚え，次のtokenと連続しているか調べる．
        end = match.end()
        # 空白は構文要素として保存せず，演算子・参照名・literalだけをtoken列へ加える．
        if not match.group().isspace():
            tokens.append(match.group())
    # 最後のtoken以降に未認識部分が残っていれば，不完全に解析した式を採用しない．
    if end != len(text):
        raise UnsupportedExpression('unrecognized expression token at ' + text[end:])
    # 終端tokenを付け，最後まで式を読んだかを確認できるようにする．
    tokens.append('<end>')
    # 次に取り出すtokenの添字を0から始め，内部関数で共有する．
    pos = 0

    def take(expected=None):
        """現在のtokenを1つ取り，読取り位置を進める．"""
        # 外側のparseが持つ読取り位置を更新するため，ローカル変数を新設せずnonlocalを宣言する．
        nonlocal pos
        # 現在位置のtokenを読み，次の一致検査とAST生成に使う．
        token = tokens[pos]
        # 閉じ括弧など期待tokenが指定されていれば一致を要求し，違う場合は例外にする．
        if expected is not None and token != expected:
            raise UnsupportedExpression(f'expected {expected!r}, got {token!r}')
        # 読取り位置を1つ進め，同じtokenを繰り返し読まないようにする．
        pos += 1
        return token

    def primary():
        """literal・参照・関数・括弧・単項演算を読む．"""
        # tokenを1個消費し，単項演算・括弧・literal・参照のどれかへ分岐する．
        token = take()
        # !の右側を再帰的に読み，否定演算の子nodeを1つ持たせる．
        if token == '!':
            result = Node('!', args=(primary(),))
        # 開き括弧の内側を独立した式として読み，対応する閉じ括弧も消費する．
        elif token == '(':
            result = binary(0)
            take(')')
        # 単引用符の文字列は外側の引用符を外し，''を文字列中の'へ戻す．
        elif token.startswith("'"):
            result = Node('literal', token[1:-1].replace("''", "'"))
        # 式中のtrue/false/nullを，PythonのTrue/False/Noneのliteralへ対応付ける．
        elif token in {'true', 'false', 'null'}:
            result = Node('literal', {'true': True, 'false': False, 'null': None}[token])
        # 数値らしいtokenは16進数またはJSONの数値として読み，不正な数値は未対応式へ変換する．
        elif re.match(r'^-?\d|^\.\d', token):
            try:
                result = Node(
                    'literal',
                    int(token, 16) if token.lower().startswith('0x') else json.loads(token)
                )
            except ValueError as exc:
                raise UnsupportedExpression(str(exc)) from exc
        elif re.fullmatch(r'[A-Za-z_][\w.-]*', token):
            # 識別子の直後に(があれば関数呼出しとして読み，なければcontext参照nodeにする．
            if tokens[pos] == '(':
                take('(')
                # 関数引数のASTを左から順にため，呼出しnodeの子として保存する．
                args = []
                # 引数が空の場合を除き，最初の式を1個読み取る．
                if tokens[pos] != ')':
                    args.append(binary(0))
                    # カンマが続く間は次の引数式を追加し，閉じ括弧で停止する．
                    while tokens[pos] == ',':
                        take(',')
                        args.append(binary(0))
                take(')')
                # 関数名を小文字へそろえ，集めた引数をtupleにしてcall nodeへ格納する．
                result = Node('call', token.lower(), tuple(args))
            else:
                # 関数呼出しでない識別子は，後段のresolverが値を与える参照nodeにする．
                result = Node('ref', token)
        else:
            raise UnsupportedExpression(f'unexpected {token!r}')
        # 添字アクセスが続く間は，直前の結果へindex nodeまたは参照名の連結を重ねる．
        while tokens[pos] == '[':
            take('[')
            index = binary(0)
            take(']')
            # env['NAME']のような参照と文字列添字は，env.NAMEと同じ共有参照へ正規化する．
            if result.kind == 'ref' and index.kind == 'literal' and isinstance(index.value, str):
                result = Node('ref', str(result.value) + '.' + index.value)
            else:
                result = Node('index', args=(result, index))
        return result
    # 数値が大きい演算子を先にまとめる．
    # 括弧内はprimaryから再びbinaryを呼んで同じ規則で読む．
    precedence = {'||': 1, '&&': 2, '==': 3, '!=': 3, '<': 4, '>': 4, '<=': 4, '>=': 4}

    def binary(minimum):
        """最低優先順位以上の二項演算を再帰的にまとめる．"""
        # 優先順位つきの二項演算を読む前に，左側の基本式を1つ作る．
        left = primary()
        # 次の演算子の優先順位が現在の下限以上なら，右側を読んで式を結合する．
        while tokens[pos] in precedence and precedence[tokens[pos]] >= minimum:
            op = take()
            # 右側を1段高い優先順位で再帰解析し，a || b && cがa || (b && c)となるようにする．
            left = Node(op, args=(left, binary(precedence[op] + 1)))
        return left
    try:
        result = binary(0)
        # 式1個を読んだ後にtokenが残れば，途中までのASTだけを返さずエラーにする．
        if tokens[pos] != '<end>':
            raise UnsupportedExpression('trailing expression: ' + tokens[pos])
        return result
    # tokenが途中で尽きた場合を，未完の式という共通の例外へ読み替える．
    except IndexError as exc:
        raise UnsupportedExpression('incomplete expression') from exc

# GitHubの式で偽となるnull・false・空文字・数値0を判定する．
def truth(value):
    """GitHub式subsetの真偽値変換を行う．"""
    # null・false・空文字・数値0をfalsyとして判定し，その他の候補はtruthyにする．
    return not (value is None or value is False or value == '' or (type(value) in { int, float } and value == 0))

def number(value):
    """loose比較に使う数値変換を行う．"""
    # nullは呼び出す変換関数に応じて，numberでは0，stringでは空文字として扱う．
    if value is None:
        return 0
    # boolはPythonではintの派生型なので，数値の分岐より先に処理して変換規則を区別する．
    if isinstance(value, bool):
        return int(value)
    # boolを含めずint/floatそのものだけを数値として扱う．
    if type(value) in {int, float}:
        return value
    if isinstance(value, str):
        # 空白だけの文字列は数値0へ変換し，JSON解析に渡さない．
        if not value.strip():
            return 0
        try:
            # 文字列をJSON数値として読めるか調べ，配列等ならNaNとして数値比較を成立させない．
            parsed = json.loads(value)
            return parsed if type(parsed) in {int, float} else math.nan
        except ValueError:
            # 数値へ変換できない型・文字列にはNaNを返し，未知の数値を勝手に補わない．
            return math.nan
    # 数値へ変換できない型・文字列にはNaNを返し，未知の数値を勝手に補わない．
    return math.nan

def string(value):
    """対応する候補値をGitHub式subsetの文字列へ変換する．"""
    # nullは呼び出す変換関数に応じて，numberでは0，stringでは空文字として扱う．
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    # boolはPythonではintの派生型なので，数値の分岐より先に処理して変換規則を区別する．
    if isinstance(value, bool):
        return str(value).lower()
    # boolを含めずint/floatそのものだけを数値として扱う．
    if type(value) in {int, float}:
        return str(value)
    raise UnsupportedExpression('array/object cannot be converted to string')

# 文字列は大小文字を区別せず比較し，型が異なる場合は数値変換を使う．
def equal(a, b):
    """大小文字と型変換を考慮した限定的な等価比較をする．"""
    # 同じ型同士なら型内の比較規則を使い，異なる型なら下で数値へそろえる．
    if type(a) == type(b):
        # 文字列比較は大小文字を区別しないため，両辺を小文字へ変換する．
        if isinstance(a, str):
            return a.lower() == b.lower()
        # object/arrayの同一性は抽象化していないため，構造比較で代用せず例外にする．
        if isinstance(a, (dict, list)):
            raise UnsupportedExpression('object/array instance identity is not abstracted')
        return a == b
    return number(a) == number(b)

# 具体的な有限候補1組について，構文木の値を計算する．
def evaluate(node, references, statuses=None):
    """具体的な参照値1組を代入してASTを評価する．"""
    # status関数の値が渡されなければ空辞書を使い，必要な呼出しだけが参照する．
    statuses = statuses or {}
    # literal nodeなら，保持している具体値をそのまま返す．
    if node.kind == 'literal':
        return node.value
    # context参照nodeなら，今回の直積割当てで選ばれた値を辞書から得る．
    if node.kind == 'ref':
        return references[str(node.value)]
    # &&/||は右側を必要な時だけ評価する．
    # 返すのは単なるBooleanではなく，選ばれた側の値である．
    if node.kind == '&&':
        left = evaluate(node.args[0], references, statuses)
        return evaluate(node.args[1], references, statuses) if truth(left) else left
    if node.kind == '||':
        left = evaluate(node.args[0], references, statuses)
        return left if truth(left) else evaluate(node.args[1], references, statuses)
    # &&/||以外では，子nodeを再帰評価して引数の具体値を左から集める．
    args = [evaluate(arg, references, statuses) for arg in node.args]
    if node.kind == '!':
        return not truth(args[0])
    if node.kind in {'==', '!='}:
        # *argsで2つの候補値を比較関数の引数へ展開し，GitHub式用の等価規則を適用する．
        result = equal(*args)
        return result if node.kind == '==' else not result
    if node.kind in {'<', '>', '<=', '>='}:
        # 比較する2つの値をアンパックし，文字列同士か数値比較かを続けて判定する．
        a, b = args
        # 文字列比較は大小文字を区別しないため，両辺を小文字へ変換する．
        if isinstance(a, str) and isinstance(b, str):
            a, b = (a.lower(), b.lower())
        else:
            a, b = (number(a), number(b))
        return {
            '<': lambda: a < b,
            '>': lambda: a > b,
            '<=': lambda: a <= b,
            '>=': lambda: a >= b
        }[node.kind]()
    # object/arrayの添字を評価し，存在しないキーや添字は空文字として扱う．
    if node.kind == 'index':
        try:
            return args[0][args[1]]
        except (KeyError, IndexError, TypeError):
            return ''
    # ここまでの演算に該当しないnodeは，関数呼出しでなければ未対応とする．
    if node.kind != 'call':
        raise UnsupportedExpression('unsupported AST node ' + node.kind)
    name = node.value
    # 登録関数に必要な引数数を表にし，誤った呼出しをPython側の偶発的なエラーへ任せない．
    arities = {
        'always': 0,
        'success': 0,
        'failure': 0,
        'cancelled': 0,
        'contains': 2,
        'startswith': 2,
        'endswith': 2,
        'fromjson': 1,
        'tojson': 1
    }
    # 登録された引数数と一致しなければ，式の意味を推測せず例外にする．
    if name in arities and len(args) != arities[name]:
        raise UnsupportedExpression(f'{name}: invalid argument count')
    # always()は常に真，他のstatus関数はschedulerが渡した今回の成否割当てを参照する．
    if name in {'always', 'success', 'failure', 'cancelled'}:
        return True if name == 'always' else statuses[name]
    # 配列なら要素の等価比較，文字列なら大小文字を無視した部分文字列一致を使う．
    if name == 'contains':
        return any((equal( x, args[1] ) for x in args[0])) if isinstance(args[0], list) else string(args[1]).lower() in string(args[0]).lower()
    if name == 'startswith':
        return string(args[0]).lower().startswith(string(args[1]).lower())
    if name == 'endswith':
        return string(args[0]).lower().endswith(string(args[1]).lower())
    if name == 'fromjson':
        try:
            return json.loads(string(args[0]))
        except ValueError as exc:
            raise UnsupportedExpression('fromJSON input is invalid JSON') from exc
    if name == 'tojson':
        return json.dumps(args[0], ensure_ascii=False, indent=2)
    # 最初の文字列を書式とし，残りの引数を文字列化して番号付きplaceholderへ展開する．
    if name == 'format' and args:
        try:
            return string(args[0]).format(*(string(x) for x in args[1:]))
        except (KeyError, IndexError, ValueError) as exc:
            raise UnsupportedExpression('format pattern outside numbered placeholder subset') from exc
    raise UnsupportedExpression('unsupported function ' + str(name))

def walk(node):
    """ASTを親から子の順に列挙するgenerator．"""
    # generatorとして現在のnodeを1つ返し，後続の参照収集側が順番に受け取れるようにする．
    yield node
    # 子nodeをそれぞれたどり，yield fromで子孫の列も順番に外へ渡す．
    for arg in node.args:
        yield from walk(arg)

def key(value):
    """候補値をまとめるためのJSON keyを作る．"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)

# 参照ごとの候補を組み合わせ，同じ値になる条件をまとめる．
# 同じ参照は1回だけ割り当てるので，同じoutputを矛盾する値として使わない．
def lower(node, resolve, statuses, limit=8192):
    """共有参照の有限domainから，結果値ごとのSMV条件を作る．"""
    # 同じ参照をsetで1つにする．
    # 同一outputを同時にtrue/falseとして割り当てないための相関保持である．
    refs = sorted({str(n.value) for n in walk(node) if n.kind == 'ref'})
    calls = sorted({str(n.value) for n in walk(node) if n.kind == 'call' and n.value in {'success', 'failure', 'cancelled'}})
    # 各参照の「値と条件」の列を次元とし，status関数にも真偽それぞれの条件を用意する．
    dimensions = [resolve(ref) for ref in refs] + [[(True, statuses[c]), (False, f'!({statuses[c]})')] for c in calls]
    if math.prod(map(len, dimensions)) > limit:
        raise UnsupportedExpression('finite expression product exceeds ' + str(limit))
    grouped = {}
    # *dimensionsで各候補列を個別引数へ展開し，直積の組を1つずつ調べる．
    for assignment in itertools.product(*dimensions):
        # zipで参照名と今回の候補値を対応付ける．
        # generator式は必要な値だけを順に取り出す．
        values = dict(zip(refs, (a[0] for a in assignment[:len(refs)])))
        # 参照名の割当てに続く部分から，status関数名と真偽の対応辞書を作る．
        status = dict(zip(calls, (a[0] for a in assignment[len(refs):])))
        # 共有参照を1組に固定した上で式全体の具体値を評価する．
        value = evaluate(node, values, status)
        # 今回選んだ全候補のguardをANDにし，参照なしの式ならTRUEを使う．
        pred = ' & '.join((f'({a[1]})' for a in assignment)) or 'TRUE'
        # 結果値を安定したJSONキーへ変換し，同じ結果の候補条件をまとめる．
        token = key(value)
        # 初めて現れた結果値にだけ，値と条件一覧の入れ物を作る．
        if token not in grouped:
            grouped[token] = [value, []]
        # 結果値が同じでも成立条件は複数あり得るので，条件をORへまとめて返す．
        grouped[token][1].append(pred)
    return [(value, '(' + ' | '.join(preds) + ')') for value, preds in grouped.values()]
