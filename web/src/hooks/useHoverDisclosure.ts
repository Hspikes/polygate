import { useEffect, useRef, useState } from "react";

export function useHoverDisclosure(openDelay = 120) {
  const [expanded, setExpanded] = useState(false);
  const hovering = useRef(false);
  const openTimer = useRef<number | null>(null);

  const clearOpenTimer = () => {
    if (openTimer.current !== null) window.clearTimeout(openTimer.current);
    openTimer.current = null;
  };

  useEffect(() => clearOpenTimer, []);

  const hoverIsAvailable = () => (
    typeof window.matchMedia !== "function"
    || window.matchMedia("(hover: hover)").matches
  );

  const openFromHover = () => {
    if (!hoverIsAvailable()) return;
    hovering.current = true;
    clearOpenTimer();
    openTimer.current = window.setTimeout(() => {
      if (hovering.current) setExpanded(true);
      openTimer.current = null;
    }, openDelay);
  };

  const closeFromHover = () => {
    if (!hoverIsAvailable()) return;
    hovering.current = false;
    clearOpenTimer();
    setExpanded(false);
  };

  const close = () => {
    clearOpenTimer();
    setExpanded(false);
  };

  const toggleFromTrigger = (eventDetail: number) => {
    if (hoverIsAvailable() && hovering.current && eventDetail > 0) return;
    clearOpenTimer();
    setExpanded((current) => !current);
  };

  return { expanded, openFromHover, closeFromHover, close, toggleFromTrigger };
}
