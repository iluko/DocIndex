import { useEffect, useMemo, useRef, useState } from "react";

type SelectOption = {
  value: string;
  label?: string;
  note?: string;
};

type SelectMenuProps = {
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
};

export function SelectMenu({
  value,
  options,
  onChange,
  disabled = false,
  placeholder = "Select…",
}: SelectMenuProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);

  const selected = useMemo(
    () => options.find((option) => option.value === value),
    [options, value],
  );

  useEffect(() => {
    if (!open) {
      return;
    }

    function handlePointerDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  return (
    <div
      className={`select-menu ${open ? "select-menu-open" : ""} ${
        disabled ? "select-menu-disabled" : ""
      }`}
      ref={rootRef}
    >
      <button
        type="button"
        className="select-trigger"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="select-trigger-label">{selected?.label ?? placeholder}</span>
        <span className="select-trigger-icon" aria-hidden="true">
          ▾
        </span>
      </button>

      {open ? (
        <div className="select-popover">
          {options.map((option) => {
            const isSelected = option.value === value;
            return (
              <button
                type="button"
                key={option.value}
                className={`select-option ${isSelected ? "select-option-selected" : ""}`}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                }}
              >
                <span className="select-option-main">
                  <span className="select-option-label">{option.label ?? option.value}</span>
                  {option.note ? <small>{option.note}</small> : null}
                </span>
                {isSelected ? <span className="select-option-check">✓</span> : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
