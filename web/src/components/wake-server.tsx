"use client";

import { useEffect } from "react";
import { wakeServer } from "@/lib/api";

/** Renders nothing. On first load it pings the API so a sleeping server starts waking up
 *  while the visitor is still reading the page. */
export function WakeServer() {
  useEffect(() => {
    void wakeServer();
  }, []);
  return null;
}
