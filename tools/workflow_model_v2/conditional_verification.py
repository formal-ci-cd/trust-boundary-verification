# 外部条件の反例値を復号し，Booleanの両側を同じモデルのINIT制限で再検査する．
"""CTLの到達性，検証完了状態，意味論coverage，条件の必要性を別々に報告する．

AG !targetのfalseを，少なくとも一つの初期条件からの到達反例として扱う．
反例に出た値はwitnessと呼び，両値の再検査が完了するまで必要条件とは呼ばない．
追加NuSMV出力も既存の統合ログへ保存し，一時SMVは処理後に削除する．
"""
from pathlib import Path
import json
import re
import tempfile


def properties(stdout):
    """NuSMVの表示式とtrue/falseを読む．表示名と命題の意味は取り違えない．"""
    return {name.strip():value for name,value in re.findall(r'^-- specification (.*?) is (true|false)',stdout,re.M)}


def decode_witness(stdout, expression, conditions):
    """該当propertyの反例に実際に表示された固定条件だけを復号する．"""
    trace = ''
    for block in re.split(r'(?=^-- specification )',stdout,flags=re.M):
        match = re.match(r'-- specification (.*?) is false',block)
        if match and match[1].strip()==expression:
            trace=block
            break
    assignments = {}
    for name,value in re.findall(r'^\s*([A-Za-z_]\w*) = (\S+)\s*$',trace,re.M):
        assignments.setdefault(name,value)
    result=[]
    for condition in conditions:
        encoded=assignments.get(condition.get('symbol'))
        if encoded in condition.get('encodings',{}):
            result.append(dict(owner=condition['owner'],name=condition['name'],
                               value=condition['encodings'][encoded],symbol=condition['symbol'],
                               origin=condition['origin'],meaning='value observed in one witness; necessity not implied'))
    return result


def restriction(condition, value):
    """意味上のBoolean値を，それに対応するprofile列挙値の条件式へ戻す．"""
    encodings=[key for key,decoded in condition['encodings'].items() if type(decoded) is bool and decoded==value]
    if not encodings:
        return None
    return '('+' | '.join(condition['symbol']+' = '+encoded for encoded in encodings)+')'


def explain(model, context, stdout, stream, run_phase, write, executable, timeout,
            property_name='no_bad_authority', check_booleans=True, max_conditions=8):
    """意味付きの反例と，完了したBoolean反対条件の検査結果を返す．"""
    external=context.get('externalConditions')
    if not external:
        return None
    conditions=external['conditions']
    raw=properties(stdout)
    by_name=context.get('properties',{})
    if property_name not in by_name:
        property_name=next((name for name,expr in by_name.items() if expr=='AG !'+property_name),None)
    if property_name is None:
        raise ValueError('requested condition-report property is not in the manifest')
    expression=by_name[property_name]
    if not re.fullmatch(r'AG ![A-Za-z_]\w*',expression):
        raise ValueError('condition reports require a named AG !target property')
    verdict=raw.get(expression)
    report=dict(property=property_name,expression=expression,
                reachability='reachable' if verdict=='false' else 'unreachable-in-modeled-domain' if verdict=='true' else 'incomplete',
                witnessConditions=decode_witness(stdout,expression,conditions) if verdict=='false' else [],
                fixedConditions=[dict(owner=c['owner'],name=c['name'],value=c['domain'][0],origin=c['origin']) for c in conditions if c.get('symbol') is None and len(c['domain'])==1],
                scope='recorded finite model only; unsupported effects and incomplete domains remain separate',
                coverage=external.get('coverage',{}),
                specifiedRestrictions=external.get('restrictions',[]),
                unsupported=[item for item in context.get('diagnostics',[]) if item['status']=='unsupported'],
                unknown=[item for item in context.get('diagnostics',[]) if item['status']=='unknown'],
                booleanChecks=[],necessaryConditionsWithinModel=[])
    if not check_booleans or verdict!='false':
        report['booleanCheckStatus']='not-requested' if not check_booleans else 'no-reachable-witness'
        return report
    candidates=[condition for condition in conditions if condition['value_type']=='boolean'
                and set(condition['domain'])=={False,True}]
    for condition in candidates[:max_conditions]:
        item=dict(owner=condition['owner'],name=condition['name'],values=[],necessity='not-established')
        for value in (False,True):
            predicate=restriction(condition,value)
            if predicate is None:
                item['values'].append(dict(value=value,status='outside-selected-domain'))
                continue
            with tempfile.TemporaryDirectory(prefix='tbv-condition-') as temporary:
                target=Path(temporary)/'restricted.smv'
                # 利用者のSMVを上書きせず，初期条件だけを限定した一時modelを検査する．
                target.write_text(model.read_text()+'\nINIT '+predicate+'\n')
                commands=(f'read_model -i {json.dumps(str(target),ensure_ascii=False)}\n'
                          f'go\ncheck_fsm\ncheck_ctlspec -P {property_name}\n'
                          'compute_reachable\nprint_reachable_states\nquit\n')
                script=Path(temporary)/'commands';script.write_text(commands)
                phase=run_phase(stream,f'条件別検査 {condition["owner"]}.{condition["name"]}={value}',
                                [executable,'-source',str(script)],timeout,commands)
                # 出力位置からこのphaseだけを読み，先行検査の成功表示を混ぜない．
                with Path(str(model)+'.log').open('rb') as saved:
                    saved.seek(phase['stdout']['offset'])
                    output=saved.read(phase['stdout']['length']).decode('utf-8',errors='replace')
                found=re.search(r'reachable states:\s*([^\n]+)',output,re.I)
                try:
                    nonempty=bool(found) and float(found[1].split()[0])>0
                except (ValueError,IndexError):
                    nonempty=False
                result=properties(output).get(expression)
                complete=(phase['exitcode']==0 and not phase['timedOut'] and nonempty
                          and 'The transition relation is total: No deadlock state exists' in output
                          and result in {'true','false'})
                item['values'].append(dict(value=value,status='complete' if complete else 'incomplete',
                                           reachable=result=='false' if complete else None,
                                           nonemptyInitialDomain=nonempty,phase=phase))
        values=item['values']
        if len(values)==2 and all(value['status']=='complete' for value in values):
            reachable=[value['value'] for value in values if value['reachable']]
            if len(reachable)==1:
                item['necessity']='necessary-within-modeled-domain'
                item['requiredValue']=reachable[0]
                report['necessaryConditionsWithinModel'].append(dict(owner=condition['owner'],name=condition['name'],value=reachable[0]))
            else:
                item['necessity']='not-necessary' if len(reachable)==2 else 'not-established'
        report['booleanChecks'].append(item)
    report['booleanCheckStatus']='complete' if len(candidates)<=max_conditions and all(
        all(value['status']=='complete' for value in item['values']) for item in report['booleanChecks']) else 'partial'
    report['uncheckedBooleanConditions']=[dict(owner=item['owner'],name=item['name']) for item in candidates[max_conditions:]]
    return report
