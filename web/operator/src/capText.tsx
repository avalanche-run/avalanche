import {
  Children,
  Fragment,
  cloneElement,
  isValidElement,
  type CSSProperties,
  type ReactNode,
} from "react";

const capStyle = {
  display: "inline-block",
  textBox: "trim-both cap alphabetic",
  // Keep ink inside the span even when the host applies truncate via a child
  // selector. Negative margins retain the cap-height alignment footprint.
  paddingBlock: "0.35em",
  marginBlock: "-0.35em",
} satisfies CSSProperties;
const lowercase = (text: string) => /\p{Ll}/u.test(text) && text === text.toLowerCase();

/** Center the cap box while reserving paint room for ascenders and descenders. */
export function capText(children: ReactNode): ReactNode {
  const labels: ReactNode[] = [];
  let labelText = "";
  const collect = (nodes: ReactNode) => Children.forEach(nodes, (child) => {
    if (typeof child === "string" || typeof child === "number") labelText += child;
    else if (isValidElement<{ children?: ReactNode; "aria-hidden"?: boolean }>(child) &&
      !child.props["aria-hidden"] && (child.type === Fragment || child.type === "span" || child.type === "strong")) {
      collect(child.props.children);
    }
  });
  collect(children);
  const lowerLabel = lowercase(labelText);
  let text = "";
  const flush = () => {
    if (text.trim()) {
      // Inline text loses a leading space inside an inline-block. Keep boundary
      // whitespace outside it so a preceding glyph still has a normal word gap.
      const leading = text.match(/^\s+/u)?.[0];
      const trailing = text.match(/\s+$/u)?.[0];
      if (leading) labels.push(leading);
      labels.push(
        <span
          key={`text-${labels.length}`}
          className="avalanche-cap-text"
          data-lowercase={lowerLabel && lowercase(text) || undefined}
          style={capStyle}
        >
          {text.trim()}
        </span>,
      );
      if (trailing) labels.push(trailing);
    }
    text = "";
  };
  const visit = (nodes: ReactNode, prefix = "") =>
    Children.forEach(nodes, (child, index) => {
      const key = `${prefix}/${isValidElement(child) && child.key !== null ? child.key : index}`;
      if (typeof child === "string" || typeof child === "number") {
        text += child;
      } else if (
        isValidElement<{
          children?: ReactNode;
          className?: string;
          style?: CSSProperties;
          "aria-hidden"?: boolean;
          "data-lowercase"?: boolean;
        }>(child)
      ) {
        if (child.type === Fragment) {
          visit(child.props.children, key);
        } else {
          flush();
          const textOnly =
            (child.type === "span" || child.type === "strong") &&
            !child.props["aria-hidden"] &&
            Children.count(child.props.children) > 0 &&
            Children.toArray(child.props.children).every(
              (part) => typeof part === "string" || typeof part === "number",
            );
          labels.push(
            cloneElement(child, {
              key,
              ...(textOnly
                ? {
                    className: `${child.props.className ?? ""} avalanche-cap-text`,
                    "data-lowercase":
                      lowerLabel && lowercase(Children.toArray(child.props.children).join("")) || undefined,
                    style: {
                      ...child.props.style,
                      ...capStyle,
                    },
                  }
                : {}),
            }),
          );
        }
      } else if (child) {
        flush();
        labels.push(child);
      }
    });
  visit(children);
  flush();
  return labels;
}
