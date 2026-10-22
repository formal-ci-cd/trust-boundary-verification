"""Lower finite shell values and integrity histories into the common transition IR."""
import hashlib
import re
from pathlib import PurePosixPath
from . import expressions as ex
from .transition import atom, symbol, conjunction as AND, disjunction as OR


class ShellLowering:
    def _prepare_shell_state(self):
        self.has_structured=any(s.program for j in self.jobs for s in j.steps)
        self.contents={};self.origins={};self.bundles={};self.output_origins={};self.integrity_files={}
        self.values=['','other']
        for run in self.runs:
            for values in run.context.get('checkoutValues',{}).values():
                self.values.extend(values)
        for job in self.jobs:
            for step in job.steps:
                self.values.extend(op[2] for op in step.ops if op[0]=='write')
        self.values=list(dict.fromkeys(self.values))
        if not self.has_structured:
            return
        for name in ['artifact_read','verification_read','post_verification_use','post_integrity_mismatch_use',
                     'artifact_dependent_use','integrity_blocked','simulated_sink_site_reached',
                     'artifact_dependent_sink','simulated_sink_result_true']:
            self.ts.var(name,['FALSE','TRUE'],'FALSE')
        self.ts.var('integrity_state',['integrity_not_run','integrity_verified','integrity_mismatch','integrity_unverifiable'],'integrity_not_run')
        self.ts.defines.update(bad_simulated_path='artifact_dependent_sink & simulated_sink_site_reached & simulated_sink_result_true',
                               bad_real_authority_path='bad_authority',download_success_any='transfer')
        for expression in ['AG !bad_simulated_path','AG !bad_real_authority_path','AG (artifact_dependent_use -> download_success_any)',
                           'AG (artifact_dependent_sink -> artifact_dependent_use)','AG !post_integrity_mismatch_use',
                           'AG !verification_read','AG !post_verification_use','AG !artifact_dependent_use',
                           'AG !simulated_sink_site_reached','AG !artifact_dependent_sink',
                           'AG (integrity_state != integrity_verified)','AG (integrity_state != integrity_mismatch)','AG !integrity_blocked']:
            self.ts.properties[symbol('property',expression)]=expression
        for job in self.jobs:
            for path in self.runs[job.run].context.get('checkoutFiles',[]):
                self.content(job,path)
            for step in job.steps:
                for operation in step.ops:
                    if operation[0]=='write': self.content(job,operation[1])
                    elif operation[0]=='copy':
                        self.content(job,operation[1]);self.content(job,operation[2])
                if not step.program:
                    continue
                domains={}
                for op in step.program.operations:
                    if op.kind=='copy':
                        for path in op.path.split('->'):
                            self.content(job,path)
                    elif op.kind=='grep-assert':
                        self.content(job,op.path)
                    elif op.kind in {'read-trim','digest','test-file'}:
                        self.content(job,self.shell_path(job,step,op.reads[0],op.path or op.value if op.kind=='test-file' else op.path or ''))
                    elif op.kind=='literal-assign':
                        domains.setdefault(op.writes[0],[]).append(str(op.value))
                    if op.kind=='read-trim':
                        domains[op.writes[0]]=list(dict.fromkeys(self.trim(v,op.value) for v in self.values))
                    elif op.kind=='digest':
                        domains[op.writes[0]]=[hashlib.sha256(v.encode()).hexdigest() for v in self.values]+['other']
                    elif op.kind=='github-output':
                        self.output(job,step.id,op.writes[0].split(':')[1],['',*domains.get(op.reads[0],['other']),'other'])
                        self.output_origin(job,step.id,op.writes[0].split(':')[1])
        self.ts.defines['bad_real_authority_path']='bad_authority'

    def content(self,job,path):
        path=str(path).removeprefix('./').rstrip('/') or '.'
        key=(job.id,path)
        self.file(job,path)
        if key not in self.contents:
            name=symbol('content',job.id+':'+path)
            self.ts.var(name,[atom(v) for v in self.values],atom(''),job.source)
            self.contents[key]=name
            origin=symbol('from_artifact',job.id+':'+path)
            self.ts.var(origin,['FALSE','TRUE'],'FALSE',job.source)
            self.origins[key]=origin
        return self.contents[key],self.origins[key]

    def output_origin(self,job,sid,name):
        key=(job.id,sid,name)
        if key not in self.output_origins:
            token=symbol('output_origin',':'.join(key))
            self.ts.var(token,['FALSE','TRUE'],'FALSE',job.source)
            self.output_origins[key]=token
        return self.output_origins[key]

    def shell_path(self,job,step,variable,suffix=''):
        values=self.resolve('env.'+variable,job,step)
        paths=list(dict.fromkeys(ex.string(v)+str(suffix or '') for v,g in values))
        if len(paths)!=1 or not paths[0]:
            self.diagnostic('shell-path-domain','file environment binding must have one static path',step.source)
            return 'unsupported-path'
        return paths[0].removeprefix('./').rstrip('/') or '.'

    def _checkout_contents(self,job,path,guard):
        if not self.has_structured:
            return
        values=self.runs[job.run].context.get('checkoutValues',{}).get(path)
        if values is None:
            values=['other']
            self.diagnostic('checkout-content-domain','finite file contents were not supplied for '+path,job.source)
        token=symbol('input_content',self.runs[job.run].id+':'+path)
        self.ts.frozen[token]=list(dict.fromkeys(atom(v) for v in values))
        content,origin=self.content(job,path)
        self.ts.update(content,guard,token)
        self.ts.update(origin,guard,'FALSE')

    def _bundle_source(self,obj,source):
        if not self.has_structured or obj['kind']!='artifact':
            return source,source+' != absent'
        root=obj['path'].rstrip('/')
        paths=[path for jid,path in self.contents if jid==obj['job'].id and (path==root or path.startswith(root+'/'))]
        self.bundles[obj['token']]=paths
        states=[self.file(obj['job'],path) for path in paths]
        exists=OR(s+' != absent' for s in states)
        tainted=OR(s+' = tainted' for s in states)
        return f'case {tainted} : tainted; {exists} : clean; TRUE : absent; esac',exists

    def _save_contents(self,obj,guard):
        if not self.has_structured or obj['kind']!='artifact':
            return
        job=obj['job'];root=obj['path'].rstrip('/')
        for path in self.bundles.get(obj['token'],[]):
            relative=path[len(root):].lstrip('/') if path!=root else PurePosixPath(path).name
            key=(obj['token'],relative)
            value=symbol('object_content',':'.join(key))
            present=symbol('object_member',':'.join(key))
            if not hasattr(self,'object_members'):
                self.object_members={}
            if key not in self.object_members:
                self.object_members[key]=(value,present,path)
                self.ts.var(value,[atom(v) for v in self.values],atom(''),obj['source'])
                self.ts.var(present,['FALSE','TRUE'],'FALSE',obj['source'])
            if guard is not None:
                self.ts.update(value,guard,self.content(job,path)[0])
                self.ts.update(present,guard,self.file(job,path)+' != absent')

    def _restore_contents(self,job,obj,destination,guard):
        if not self.has_structured:
            return
        if obj['kind']!='artifact':
            if any(s.program and s.program.artifact_reads for s in job.steps):
                self.diagnostic('cache-content-domain','structured Cache byte/digest contracts are not supplied; taint-only cache restoration remains modeled',job.source)
            return
        for (token,relative),(value,present,path) in getattr(self,'object_members',{}).items():
            if token!=obj['token']:
                continue
            targets=[destination.rstrip('/')+'/'+relative]
            if path==obj['path']:
                targets.append(destination)
            for target in targets:
                content,origin=self.content(job,target)
                effect=AND(guard,present)
                self.ts.update(self.file(job,target),effect,obj['token'])
                self.ts.update(content,effect,value)
                self.ts.update(origin,effect,'TRUE')
                self.ts.update(self.file_integrity(job,target),effect,'integrity_not_run')

    def file_integrity(self,job,path):
        key=(job.id,path)
        if key not in self.integrity_files:
            name=symbol('integrity',job.id+':'+path)
            self.ts.var(name,['integrity_not_run','integrity_verified','integrity_mismatch','integrity_unverifiable'],'integrity_not_run',job.source)
            self.integrity_files[key]=name
        return self.integrity_files[key]

    def _blocked_history(self,job,step,gate,condition):
        for op in step.program.operations:
            if op.kind=='read-trim':
                path=self.shell_path(job,step,op.reads[0],op.path or '')
                integrity=self.file_integrity(job,path)
                self.ts.latch('integrity_blocked',AND(gate,'!('+condition+')',integrity+' = integrity_mismatch'))

    @staticmethod
    def trim(value,characters):
        chars=str(characters or '').replace(r'\r','\r').replace(r'\n','\n')
        return str(value).translate(str.maketrans('','',chars))

    def _structured_shell(self,job,step,guard):
        local_integrity={}
        def integrity(path):
            return local_integrity.get(path,self.file_integrity(job,path))
        written=set()
        for operation in step.program.operations:
            if operation.kind=='copy':
                src,dst=operation.path.split('->')
                if src in written or dst in written:
                    self.diagnostic('shell-sequential-effects','dependent file updates within one structured step require filesystem microstates',step.source)
                written.add(dst)
        if not any(o.kind=='strict-mode' for o in step.program.operations) and len(step.program.operations)>1 and step.program.artifact_reads:
            self.diagnostic('shell-failure-mode','multi-command artifact reads require set -eu for failure continuation semantics',step.source)
        local={};provenance={};stack=[];active='TRUE';failures=[];metadata_refs=set();tested='FALSE'
        digest_values={};digest_origins={};digest_paths={};trusted=self.runs[job.run].context.get('workflowTrusted')
        trust=symbol('workflow_trust',self.runs[job.run].id)
        if trusted is None:
            self.ts.frozen[trust]=['FALSE','TRUE']
        else:
            trust=str(bool(trusted)).upper()
        def env(name):
            if name in local:
                return local[name],provenance.get(name,'FALSE')
            values=self.resolve('env.'+name,job,step)
            raw={**self.runs[job.run].raw.get('env',{}),**job.raw.get('env',{}),**step.raw.get('env',{})}.get(name,'')
            ref=re.fullmatch(r'\$\{\{\s*steps\.([\w-]+)\.outputs\.([\w-]+)\s*}}',str(raw))
            origin=self.output_origin(job,ref[1],ref[2]) if ref else 'FALSE'
            if ref:
                metadata_refs.add((ref[1],ref[2]))
            return values,origin
        def assign(name,values,origin):
            previous=local.get(name,[('', 'TRUE')]);old_origin=provenance.get(name,'FALSE')
            local[name]=[(v,AND(active,g)) for v,g in values]+[(v,AND('!('+active+')',g)) for v,g in previous]
            provenance[name]=OR([AND(active,origin),AND('!('+active+')',old_origin)])
        for op in step.program.operations:
            effect=AND(guard,active)
            kind=op.kind
            if kind in {'strict-mode','block','summary-redirection','case-next'}:
                continue
            if kind=='literal-assign':
                origin=OR(frame.get('origin','FALSE') for frame in stack)
                assign(op.writes[0],[(str(op.value),'TRUE')],origin)
            elif kind in {'if-eq','if-eq-var'}:
                left,origin=env(op.reads[0])
                right,ro=env(op.reads[1]) if kind=='if-eq-var' else ([(op.value,'TRUE')],'FALSE')
                predicate=OR(AND(a,b) for x,a in left for y,b in right if x==y)
                stack.append(dict(kind='if',parent=active,predicate=predicate,origin=OR([origin,ro])))
                active=AND(active,predicate)
                if kind=='if-eq-var' and any(v in digest_values for v in op.reads):
                    variable=next(v for v in op.reads if v in digest_values)
                    origin=digest_origins[variable]
                    for state_name in ('integrity_state',self.file_integrity(job,digest_paths[variable])):
                        self.ts.update(state_name,AND(effect,origin,'!('+trust+')'),'integrity_unverifiable')
                        self.ts.update(state_name,AND(effect,origin,trust,predicate),'integrity_verified')
                        self.ts.update(state_name,AND(effect,origin,trust,'!('+predicate+')'),'integrity_mismatch')
                    path=digest_paths[variable]
                    local_integrity[path]=(f'case {AND(effect,origin,"!("+trust+")")} : integrity_unverifiable; '
                                           f'{AND(effect,origin,trust,predicate)} : integrity_verified; '
                                           f'{AND(effect,origin,trust,"!("+predicate+")")} : integrity_mismatch; '
                                           f'TRUE : {integrity(path)}; esac')
            elif kind=='else':
                frame=stack[-1];active=AND(frame['parent'],'!('+frame['predicate']+')')
            elif kind=='fi':
                active=stack.pop()['parent']
            elif kind=='case':
                stack.append(dict(kind='case',parent=active,variable=op.reads[0],origin=env(op.reads[0])[1]))
            elif kind=='case-pattern':
                frame=stack[-1];values,_=env(frame['variable']);pattern=op.value or ''
                charmatch=re.search(r'\*\[!([^]]+)\]\*',pattern)
                if not charmatch:
                    self.diagnostic('shell-case-pattern','only rejecting empty/invalid-character metadata patterns are modeled',step.source)
                    active='FALSE'
                else:
                    invalid=OR(g for v,g in values if not v or re.search('[^'+charmatch[1]+']',str(v)))
                    active=AND(frame['parent'],invalid)
            elif kind=='esac':
                frame=stack.pop();active=AND(frame['parent'],'!('+OR(failures)+')')
            elif kind=='exit':
                if op.value=='1':
                    failures.append(active)
                active='FALSE'
            elif kind in {'read-trim','digest','test-file'}:
                path=self.shell_path(job,step,op.reads[0],op.value if kind=='test-file' else op.path or '')
                content,origin=self.content(job,path);state=self.file(job,path)
                exists=state+' != absent'
                failures.append(AND(active,'!('+exists+')'))
                active=AND(active,exists)
                effect=AND(guard,active)
                self.ts.latch('artifact_read',AND(effect,origin))
                if kind=='test-file':
                    tested=OR([tested,origin]);continue
                if kind=='read-trim':
                    values=[(self.trim(v,op.value),f'{content} = {atom(v)}') for v in self.values]
                    self.ts.latch('artifact_dependent_use',AND(effect,origin))
                    self.ts.latch('dependent_use',AND(effect,state+' = tainted'))
                    self.ts.latch('post_verification_use',AND(effect,origin,'('+integrity(path)+') = integrity_verified'))
                    self.ts.latch('post_integrity_mismatch_use',AND(effect,origin,'('+integrity(path)+') = integrity_mismatch'))
                else:
                    values=[(hashlib.sha256(v.encode()).hexdigest(),f'{content} = {atom(v)}') for v in self.values]
                    digest_values[op.writes[0]]=values;digest_origins[op.writes[0]]=origin;digest_paths[op.writes[0]]=path
                    self.ts.latch('verification_read',AND(effect,origin))
                assign(op.writes[0],values,origin)
            elif kind=='test-length':
                values,origin=env(op.reads[0]);valid=OR(g for v,g in values if len(str(v))==int(op.value))
                failures.append(AND(active,'!('+valid+')'));active=AND(active,valid)
            elif kind=='grep-assert':
                content,_=self.content(job,op.path)
                valid=OR(f'{content} = {atom(v)}' for v in self.values if re.search(op.value,v.strip('\r\n')))
                failures.append(AND(active,'!('+valid+')'));active=AND(active,valid)
            elif kind=='mkdir':
                for path in op.path.split('|'):
                    self.ts.update(self.file(job,path),effect,'clean')
            elif kind=='copy':
                source,target=op.path.split('->');state=self.file(job,source)
                content,origin=self.content(job,source);dest,do=self.content(job,target)
                exists=state+' != absent';failures.append(AND(active,'!('+exists+')'));active=AND(active,exists)
                self.ts.update(self.file(job,target),AND(guard,active),state)
                self.ts.update(dest,AND(guard,active),content)
                self.ts.update(do,AND(guard,active),origin)
                self.ts.update(self.file_integrity(job,target),AND(guard,active),self.file_integrity(job,source))
            elif kind=='github-output':
                values,origin=env(op.reads[0]);key=op.writes[0].split(':')[1]
                token,_=self.output(job,step.id,key,['',*[ex.string(v) for v,g in values],'other'])
                digest_related=bool(digest_values) and any(c.kind=='if-eq-var' for c in step.program.operations)
                for value,condition in values:
                    self.ts.update(token,AND(effect,condition,trust if digest_related else 'TRUE'),atom(ex.string(value)))
                if digest_related:
                    self.ts.update(token,AND(effect,'!('+trust+')'),atom('other'))
                self.ts.update(self.output_origin(job,step.id,key),effect,origin)
            elif kind in {'echo','printf'}:
                text=op.value or ''
                marker=re.search(r'(dummy_publish_authority_reached|dummy_repository_update_reached)=([^\x27\"]+)',text)
                if not marker:
                    for name in op.reads:
                        raw=step.raw.get('env',{}).get(name,'')
                        if '.outputs.' in str(raw) and 'artifact-digest' not in str(raw):
                            env(name)
                    continue
                raw=marker[2].strip()
                if raw.startswith('$'):
                    values,origin=env(raw[1:]);true=OR(g for v,g in values if str(v)=='true')
                    dep=origin
                else:
                    true='TRUE' if raw=='true' else 'FALSE';dep='FALSE'
                    if marker[1]=='dummy_repository_update_reached' and raw=='true':
                        required={(s.id,o.writes[0].split(':')[1]) for s in job.steps if s is not step and s.program for o in s.program.output_writes if any(v.kind=='read-trim' and v.writes==o.reads for v in s.program.operations)}
                        if required and required.issubset(metadata_refs):
                            dep=AND(tested,*(self.output_origin(job,s,k) for s,k in sorted(required)))
                        else:
                            self.diagnostic('uncontracted-simulated-sink','marker lacks a complete artifact metadata output flow and file-presence test',step.source)
                # A literal false record is not a simulated authority site.
                if raw=='false':
                    continue
                self.ts.latch('simulated_sink_site_reached',effect)
                self.ts.latch('simulated_sink_result_true',AND(effect,true))
                self.ts.latch('artifact_dependent_sink',AND(effect,true,dep))
        return AND(active,'!('+OR(failures)+')'),OR(failures)
