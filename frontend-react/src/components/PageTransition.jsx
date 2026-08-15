import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";

// Nav order, so the slide direction can match where you came from.
const ORDER = ["/", "/nurse", "/patient", "/critical"];

/**
 * Direction-aware section slide.
 *
 * Moving forward through the nav pushes the new section in from the right,
 * moving back brings it from the left. A single fade in both directions loses
 * that cue, and the interface stops telling you where you are in the stack.
 *
 * Remounted on pathname via `key`, which is what restarts the animation - CSS
 * alone cannot re-run on a node React is reusing.
 */
export default function PageTransition({ children }) {
  const location = useLocation();
  const previous = useRef(ORDER.indexOf(location.pathname));

  const index = ORDER.indexOf(location.pathname);
  const forward = index >= previous.current;

  useEffect(() => {
    previous.current = index;
  }, [index]);

  return (
    <div
      key={location.pathname}
      className={forward ? "slide-right" : "slide-left"}
      style={{ height: "100%", minHeight: 0 }}
    >
      {children}
    </div>
  );
}
