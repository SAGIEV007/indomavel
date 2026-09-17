// @ts-check
// A tela abre, conversa com o Chub de verdade e mostra os blocos de um vídeo ao clicar.
import { test, expect } from '@playwright/test';

test('tela abre com o Chub conectado e a lista de vídeos', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveTitle(/Indomável/);
  await expect(page.locator('#status-chub')).toHaveClass(/chip-ok/, { timeout: 30_000 });
  await expect(page.locator('#lista-videos li.video').first()).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('#faixa-servidor')).toBeHidden();
});

test('clicar num vídeo abre os blocos dele', async ({ page }) => {
  await page.goto('/');
  const primeiro = page.locator('#lista-videos li.video:not(.indisponivel)').first();
  await expect(primeiro).toBeVisible({ timeout: 30_000 });
  await primeiro.click();
  await expect(page.locator('#lista-blocos li.bloco').first()).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('#lista-blocos li.erro-lista')).toHaveCount(0);
});
