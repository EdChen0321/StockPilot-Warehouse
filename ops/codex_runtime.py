"""Private stdio connection to the official runtime. No auth token is read by ERP."""
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path


def runtime_executable():
    bundled = Path(__file__).resolve().parents[1] / 'runtime' / 'codex.exe'
    if bundled.is_file():
        return str(bundled)
    direct = shutil.which('codex.exe')
    if direct:
        return direct
    npm = Path(os.environ.get('APPDATA', '')) / 'npm/node_modules/@openai'
    found = sorted(npm.glob('**/codex.exe')) if npm.is_dir() else []
    if not found:
        raise ValueError('找不到官方 Codex runtime，請使用包含 runtime 的 Windows MVP 安裝包。')
    return str(found[0])


class CodexRuntime:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.process = None
        self.lock = threading.Lock()
        self.counter = 0

    def start(self):
        if self.process and self.process.poll() is None:
            return
        home = self.directory / 'codex'
        work = self.directory / 'empty-workspace'
        home.mkdir(parents=True, exist_ok=True)
        work.mkdir(parents=True, exist_ok=True)
        # Dedicated home: no reuse of the developer's login, MCPs, skills or plugins.
        disabled = ['shell_tool','unified_exec','apply_patch_freeform','js_repl','code_mode',
                    'code_mode_host','apps','connectors','plugins','multi_agent','collab',
                    'browser_use','browser_use_external','computer_use','image_generation',
                    'view_image','memories','memory_tool','skill_search','tool_search',
                    'hooks','codex_hooks','plugin_hooks','remote_control','search_tool',
                    'stable_environment_tools','request_permissions','request_permissions_tool']
        config = 'approval_policy = "never"\nsandbox_mode = "read-only"\nweb_search = "disabled"\ncli_auth_credentials_store = "keyring"\n[features]\n'
        config += ''.join(f'{name} = false\n' for name in disabled)
        (home / 'config.toml').write_text(config, encoding='utf-8')
        env = {k:v for k,v in os.environ.items() if k not in ('OPENAI_API_KEY','CODEX_API_KEY','OPENAI_BASE_URL','CODEX_ACCESS_TOKEN')}
        env['CODEX_HOME'] = str(home)
        self.process = subprocess.Popen([runtime_executable(), 'app-server'], cwd=work, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.messages = queue.Queue()
        def read(proc, messages):
            try:
                for line in proc.stdout:
                    try: messages.put(json.loads(line))
                    except ValueError: pass
            finally: messages.put(None)
        threading.Thread(target=read,args=(self.process,self.messages),daemon=True).start()
        self.rpc('initialize',dict(clientInfo=dict(name='erp_mvp',version='0.1.0'),capabilities=dict(experimentalApi=True)))
        self.send(dict(method='initialized'))

    def send(self, value):
        self.process.stdin.write(json.dumps(value,ensure_ascii=False)+'\n')
        self.process.stdin.flush()

    def receive(self, deadline):
        try: item=self.messages.get(timeout=max(0.01,deadline-time.monotonic()))
        except queue.Empty: raise ValueError('Codex 回應逾時，未自動重試。') from None
        if item is None: raise ValueError('Codex runtime 已中斷，請重新連線。')
        if 'method' in item and 'id' in item:
            self.send(dict(id=item['id'],error=dict(code=-32601,message='ERP does not authorize runtime tools or approval requests')))
            raise ValueError('模型要求了未開放的操作，已拒絕。')
        return item

    def rpc(self, method, params=None, timeout=30):
        self.counter += 1
        identity = self.counter
        self.send(dict(id=identity,method=method,params=params or {}))
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            item=self.receive(deadline)
            if item.get('id')==identity:
                if 'error' in item: raise ValueError('Codex 無法完成要求，請檢查登入、模型或 runtime 版本。')
                return item['result']
        raise ValueError('Codex 回應逾時，未自動重試。')

    def close(self):
        if self.process:
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired: self.process.kill()
            self.process=None

    def generate(self, model, effort, instructions, prompt, schema=None):
        self.start()
        thread = self.rpc('thread/start',dict(model=model,ephemeral=True,
            cwd=str(self.directory/'empty-workspace'),sandbox='read-only',approvalPolicy='never',
            environments=[],dynamicTools=[],baseInstructions=instructions))['thread']['id']
        params=dict(threadId=thread,input=[dict(type='text',text=prompt)],effort=effort)
        if schema is not None: params['outputSchema']=schema
        self.rpc('turn/start',params)
        deadline=time.monotonic()+90
        text=[]
        try:
            while time.monotonic()<deadline:
                event=self.receive(deadline)
                data=event.get('params',{})
                if data.get('threadId')!=thread: continue
                if event.get('method')=='item/completed':
                    item=data.get('item',{})
                    if item.get('type')=='agentMessage': text.append(item.get('text',''))
                    elif item.get('type') not in ('userMessage','reasoning'):
                        raise ValueError('runtime 嘗試使用未開放工具，已停止此對話。')
                if event.get('method')=='turn/completed':
                    if data.get('turn',{}).get('status')!='completed':
                        raise ValueError('AI 未完成回答，請確認模型權限與剩餘額度。')
                    answer='\n'.join(text).strip()
                    if not answer: raise ValueError('模型未傳回文字答案。')
                    return answer
            raise ValueError('AI 回答逾時，未自動重試。')
        finally:
            # Ephemeral process lifecycle bounds sessions, residual context and late replies.
            self.close()
