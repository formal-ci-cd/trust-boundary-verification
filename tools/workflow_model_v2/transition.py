# YAMLやActionに依存しない，共通の状態遷移表現とSMV出力．
# VARは実行中の状態，FROZENVARは実行中に固定する未知条件，IVARは各遷移で選ぶ入力に使う．
# 各変数に初期値とguard付きの更新規則を持たせ，CTL propertyと一緒に出力する．
"""共通の状態遷移IRを保持し，NuSMVの構文へ出力する．

入力は各moduleが登録する変数・初期値・guard付き更新・制約・CTL式，出力はSMV全文である．
VARは変化する状態，FROZENVARは実行中に固定する条件，IVARは遷移ごとに選ばれる入力に使う．
DEFINEは状態を増やさない名前付き式，ASSIGNはinit/next，TRANSは許す遷移の制約である．
CTLSPECはNuSMVへ検査させる性質を表す．caseは最初に成立したguardを選ぶ．
YAMLやActionを直接読まず，compilerとmixinが意味規則を登録してからrenderを呼ぶ．
登録した式のSMV構文や到達性をPythonで判定することはせず，検証はNuSMVへ分担する．
"""
from __future__ import annotations
import hashlib
import json

# 元の値から安定したSMV識別子を作る．
# このhashはArtifactの完全性検査とは別である．
def symbol(prefix, value):
    """値から，繰返し使えるSMV識別子を作る．"""
    # 値を文字列・UTF-8へ変換してSHA-256の先頭12桁を使い，同じ入力から同じSMV識別子を得る．
    return prefix + '_' + hashlib.sha256(str(value).encode()).hexdigest()[:12]

def atom(value):
    """Pythonの値をSMVの列挙型に使う名前へ変換する．"""
    # 例えば文字列'1'と数値1を区別できるよう，先にJSONへ変換してから列挙値の名前を作る．
    return symbol('v', json.dumps(value, sort_keys=True))

def conjunction(*terms):
    """複数のSMV条件をANDで結ぶ．"""
    # 各条件を括弧で囲んで&で連結し，ANDする条件が0個なら恒真のTRUEを返す．
    return '(' + ' & '.join(('(' + str(t) + ')' for t in terms)) + ')' if terms else 'TRUE'

def disjunction(terms):
    """反復可能な条件列をORで結ぶ．"""
    # generatorも最後まで消費してリストへ変え，条件が空かを判定できるようにする．
    terms = list(terms)
    # 各条件を括弧で囲んでORにし，選択肢が0個なら成立しないFALSEを返す．
    return '(' + ' | '.join(('(' + str(t) + ')' for t in terms)) + ')' if terms else 'FALSE'

class TransitionSystem:
    """状態宣言・条件付き更新・制約・propertyを格納する共通IR．"""

    def __init__(self):
        """各SMV構文に対応する空の登録表を作る．"""
        # 各登録表はSMVの役割ごとに分ける．
        # 同じ条件式でも状態を持つVARと単なるDEFINEを混同しない．
        # VARの名前から「候補domain・初期値」への対応を保存する．
        self.variables = {}
        # FROZENVARの名前と候補を保存し，実行中に変わらない未知の設定・入力内容を表す．
        self.frozen = {}
        # IVARの名前と候補を保存し，actorや成否など各遷移の選択を表す．
        self.inputs = {}
        # VARごとにguardと次状態の組を登録順で保存し，SMVのcaseの優先順にする．
        self.rules = {}
        # 状態を持たない式の別名を保存し，renderでDEFINEへ出力する．
        self.defines = {}
        # property名とCTL式を保存し，renderでCTLSPECへ出力する．
        self.properties = {}
        # 状態変数名から元YAMLの位置を引けるよう，source map用の表を持つ．
        self.sources = {}
        # 全遷移に課す条件を順に保存し，renderでTRANSへ出力する．
        self.constraints = []

    # 状態変数の有限domainと初期値を登録し，必要ならsource位置も保存する．
    def var(self, name, values, initial, source=None):
        """domain，初期値，sourceを持つVARを登録する．"""
        # 候補の最初の出現順を保って重複を除き，安定した列挙型を作る．
        values = list(dict.fromkeys(values))
        # 候補が1つだけだとNuSMVが定数化しnextを拒むため，補助enum値を付ける．
        if len(values) == 1:
            values.append('unreachable_' + name)
        # このVARの候補と初期値を登録し，状態の型と初期化に使う．
        self.variables[name] = (values, initial)
        # 新しく登録するVARの更新規則一覧を空から始める．
        self.rules[name] = []
        # source指定がある場合だけ，この記号とYAML位置の対応も登録する．
        if source:
            self.sources[name] = source
        return name

    # guardが成立した場合の次状態を追加する．
    # 先に登録した規則が優先される．
    def update(self, name, guard, value):
        """条件に対応する次状態の規則を末尾へ追加する．"""
        # SMVのcaseは上から評価される．
        # 重なるguardの優先順を呼出し側で意識する必要がある．
        self.rules[name].append((guard, value))

    # 一度起きた事象をTRUEのまま保持する．
    # 後続stepで過去の履歴を消さない．
    def latch(self, name, guard):
        """事象が成立したらTRUEを保持する更新を追加する．"""
        # 通常の更新規則を使って事象成立時にTRUEへし，成立しない時はrenderの自己保持規則に任せる．
        self.update(name, guard, 'TRUE')

    # 宣言・初期値・next規則・制約・CTLをSMVへ出力する．
    # 更新条件がない時は現状態を保つ．
    def render(self):
        """登録したIRをNuSMVの構文へ順に出力する．"""
        # モデルの前提を示すコメントと，NuSMVの入口であるMODULE mainから出力を始める．
        lines = ['-- Finite bounded runs; nondeterministic outcomes are not observations.', 'MODULE main']
        # FROZENVARは実行中一定の未知条件，IVARは各遷移で選ばれる入力．
        # どちらもこのループではinit/nextを付けない．
        for heading, decls in [('FROZENVAR', self.frozen), ('IVAR', self.inputs)]:
            # 宣言が空の種類は見出しごと省略し，空の宣言節を作らないようにする．
            if decls:
                # FROZENVARまたはIVARの見出しを出し，後続の候補宣言をその種類へ所属させる．
                lines.append(heading)
                # 登録表から変数名と候補列を1組ずつ取り出し，Booleanまたは列挙型の宣言を書く．
                for name, domain in decls.items():
                    # 候補がFALSE/TRUEならboolean，それ以外なら{候補, ...}の列挙型として宣言する．
                    lines.append(f'  {name} : ' + ('boolean' if domain == ['FALSE', 'TRUE'] else '{ ' + ', '.join(domain) + ' }') + ';')
        # VARは状態として保存する値．
        # PC，file/object，output，履歴等がここに宣言される．
        lines.append('VAR')
        # VARの候補だけを使い，ここではまだ使わない初期値を_で受け取る．
        for name, (domain, _) in self.variables.items():
            # 候補がFALSE/TRUEならboolean，それ以外なら{候補, ...}の列挙型として宣言する．
            lines.append(f'  {name} : ' + ('boolean' if domain == ['FALSE', 'TRUE'] else '{ ' + ', '.join(domain) + ' }') + ';')
        # DEFINEは既存の状態・入力から計算する式への名前であり，新しい状態変数を増やさない．
        if self.defines:
            # 式の別名がある場合だけDEFINE節を出し，計算済みの状態変数を増やさず式を再利用する．
            lines.append('DEFINE')
            # 名前と式の組からDEFINEの代入行を生成し，generatorの各行を出力一覧へ追加する．
            lines.extend((f'  {n} := {expr};' for n, expr in self.defines.items()))
        # ASSIGNに各VARのinit（初期値）とnext（次状態）を書く．
        # f文字列の{...}はPython側の文字列埋込み．
        lines.append('ASSIGN')
        # 候補domainは宣言済みなので，このループではVAR名と初期値だけを使う．
        for name, (_, initial) in self.variables.items():
            # initで初期状態を指定し，nextのcaseを開いて次状態の優先規則を並べる．
            lines.extend([f'  init({name}) := {initial};', f'  next({name}) := case'])
            # caseは上から最初に成立したguardを選ぶ．
            # updateを登録した順序が優先順位になる．
            lines.extend((f'    {guard} : {value};' for guard, value in self.rules[name]))
            # 最後のTRUE条件は現状態を返す．
            # 更新対象でないVARの値が勝手に変化しないようにする．
            lines.extend([f'    TRUE : {name};', '  esac;'])
        # TRANSは許される遷移を絞る条件．
        # 候補選択の整合や固定した成否入力を，全遷移に課す．
        lines.extend(('TRANS ' + constraint for constraint in self.constraints))
        # CTLSPECは検査する性質．
        # 文字列を書き出すだけで，true/falseや反例はNuSMVが求める．
        lines.extend((f'CTLSPEC NAME {name} := {expr};' for name, expr in self.properties.items()))
        return '\n'.join(lines) + '\n'
