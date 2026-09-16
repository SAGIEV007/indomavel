// @ts-check
import { defineConfig, devices } from '@playwright/test';

// Testes de ponta a ponta da tela do Indomável (os testes Python ficam em tests/).
// Sobe uma cópia na porta 5056 para não derrubar o servidor aberto na 5055.
const PORTA = process.env.PORTA_E2E || '5056';

export default defineConfig({
  testDir: './e2e',
  // Um teste por vez: o servidor tem uma fila única de trabalhos pesados.
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  timeout: 60_000,
  use: {
    baseURL: `http://127.0.0.1:${PORTA}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1400, height: 900 } },
    },
  ],
  webServer: {
    command: `.venv\\Scripts\\python.exe rodar.py --porta ${PORTA}`,
    url: `http://127.0.0.1:${PORTA}/api/vivo`,
    reuseExistingServer: true,
    timeout: 120_000,
    env: { INDOMAVEL_SEM_NAVEGADOR: '1' },
  },
});
