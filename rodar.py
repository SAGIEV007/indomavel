"""Inicia o servidor do Indomável.

Uso: .venv\\Scripts\\python.exe rodar.py [--porta 5056]
A porta padrão vem do .env (5055). --porta serve para testar uma cópia sem derrubar a que está aberta.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if "--porta" in sys.argv:
    os.environ["PORTA"] = sys.argv[sys.argv.index("--porta") + 1]

from indomavel.servidor import main  # noqa: E402

main()
