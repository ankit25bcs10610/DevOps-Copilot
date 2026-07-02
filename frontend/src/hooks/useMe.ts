import { useEffect, useState } from "react";

import { getMe } from "../api";
import type { Me } from "../types";

const FALLBACK: Me = {
  multi_tenant: false,
  authenticated: true,
  org: null,
  role: "owner",
  label: "Operator",
};

/** The current identity from GET /me (falls back to the single-tenant operator).
 *  Replaces the previously hard-coded account label. */
export function useMe(): Me {
  const [me, setMe] = useState<Me>(FALLBACK);
  useEffect(() => {
    let alive = true;
    getMe()
      .then((m) => alive && setMe(m))
      .catch(() => {
        /* keep the fallback identity on error */
      });
    return () => {
      alive = false;
    };
  }, []);
  return me;
}
