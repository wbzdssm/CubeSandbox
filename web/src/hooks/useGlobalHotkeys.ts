// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Tencent. All rights reserved.

/**
 * useGlobalHotkeys — mounts a single keydown listener for all global shortcuts.
 *
 * Shortcuts:
 *   ⌘K / Ctrl+K   → Command Palette
 *   ?              → Settings / Shortcuts tab
 *   R              → Refetch all queries (React Query)
 *   Escape         → Close Command Palette
 */

import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useCommandPaletteStore } from '@/store/ui';

export function useGlobalHotkeys() {
  const nav = useNavigate();
  const { t } = useTranslation('common');
  const qc = useQueryClient();
  const openPalette = useCommandPaletteStore((s) => s.open);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
<<<<<<< HEAD
      const isInput =
        tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement).isContentEditable;
=======
      const isInput = tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement).isContentEditable;
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)

      // ── ⌘K / Ctrl+K → Command Palette ──────────────────────────────
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        openPalette(true);
        return;
      }

      // ── Escape → Close Palette ──────────────────────────────────────
      if (e.key === 'Escape') {
        openPalette(false);
        return;
      }

      // ── Skip remaining shortcuts when focus is in an input ──────────
      if (isInput || e.metaKey || e.ctrlKey || e.altKey) return;

      // ── R → Refetch all active queries ──────────────────────────────
      if (e.key.toLowerCase() === 'r') {
        e.preventDefault();
        void qc.refetchQueries({ type: 'active' });
<<<<<<< HEAD
        window.dispatchEvent(
          new CustomEvent('cube:toast', { detail: { message: t('refreshed') } }),
        );
=======
        window.dispatchEvent(new CustomEvent('cube:toast', { detail: { message: t('refreshed') } }));
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
        return;
      }

      // ── ? → Settings / Shortcuts ────────────────────────────────────
      if (e.key === '?') {
        e.preventDefault();
        nav('/settings?tab=shortcuts');
        return;
      }
    };

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [nav, qc, openPalette]);
}
