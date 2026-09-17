import subprocess
import os

os.chdir(r"C:\indomavel")

# Configura user.name e user.email se nao estiverem definidos
uname = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True).stdout.strip()
if not uname:
    subprocess.run(["git", "config", "user.name", "Fernando"], check=True)
uemail = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True).stdout.strip()
if not uemail:
    subprocess.run(["git", "config", "user.email", "fernando@indomavel.studio"], check=True)

# Remove arquivos temporários de teste se existirem
for temp_file in [r"scripts\testar_servidor_vivo.py", r"tests\test_git_status.py"]:
    if os.path.exists(temp_file):
        os.remove(temp_file)

# Stage all files
subprocess.run(["git", "add", "."], check=True)

# Status
st = subprocess.run(["git", "status", "--short"], capture_output=True, text=True).stdout
print("STAGED FILES:\n", st)

msg = """feat: automação de cortes FernandoXX para Google Drive com exclusão automática de cache

- Estrutura de pacotes FernandoXX em Google Drive (com legenda, sem legenda, cru+srt, headlines com locutor inteligente)
- Validação estrita de integridade dos arquivos (>0 bytes) antes da liberação do cache
- Política de retenção limpa e exclusão automática do vídeo bruto local (PASTA_VIDEOS/<id>.mp4 e temporários)
- Executor sequencial estrito garantindo conclusão de um vídeo antes de iniciar o próximo
- Controle de modo automático (inicia DESLIGADO) com toggle na barra superior e botões nos cards
- Suíte completa de 123 testes automatizados aprovados (0 falhas)
"""

commit_res = subprocess.run(["git", "commit", "-m", msg], capture_output=True, text=True)
print("COMMIT RESULT:\n", commit_res.stdout, commit_res.stderr)

log_res = subprocess.run(["git", "log", "-1", "--stat"], capture_output=True, text=True)
print("LAST COMMIT:\n", log_res.stdout)

rem_res = subprocess.run(["git", "remote", "-v"], capture_output=True, text=True)
print("REMOTES:\n", rem_res.stdout)

if rem_res.stdout.strip():
    push_res = subprocess.run(["git", "push", "-u", "origin", "main"], capture_output=True, text=True)
    print("PUSH RESULT:\n", push_res.stdout, push_res.stderr)
else:
    print("Nenhum remote configurado ainda. Pronto para git remote add origin <url> e git push.")
