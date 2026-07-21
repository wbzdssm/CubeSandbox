// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Tencent. All rights reserved.

import { cn } from '@/lib/utils';

export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
<<<<<<< HEAD
  return <div className={cn('animate-pulse-soft rounded-md bg-muted/50', className)} {...props} />;
=======
  return (
    <div
      className={cn('animate-pulse-soft rounded-md bg-muted/50', className)}
      {...props}
    />
  );
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
}
