// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Tencent. All rights reserved.

import type { resources } from './resources';

declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'common';
<<<<<<< HEAD
    resources: (typeof resources)['en'];
=======
    resources: typeof resources['en'];
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
  }
}
