type Option = {
    value: string;
    label: string;
};

type CommonSelectProps = {
    id: string;
    name: string;
    value: string;
    options?: Option[];
    className?: string;
    disabled?: boolean;
    onChange?: (value: string) => void;
};

function CommonSelect({
                          id,
                          name,
                          value,
                          options = [],
                          className,
                          disabled = false,
                          onChange,
                      }: CommonSelectProps) {
    return (
        <select
            id={id}
            name={name}
            value={value}
            className={className}
            disabled={disabled}
            onChange={(e) => onChange?.(e.target.value)}
        >
            {options?.map((option) => (
                <option key={option.value + option.label} value={option.value}>
                    {option.label}
                </option>
            ))}
        </select>
    );
}

export default CommonSelect;