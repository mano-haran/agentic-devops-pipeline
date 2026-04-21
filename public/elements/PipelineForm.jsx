import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import React, { useMemo, useState } from "react";

export default function PipelineForm() {
  const [values, setValues] = useState(() => {
    const init = {};
    (props.fields || []).forEach((f) => {
      init[f.id] = f.value || "";
    });
    return init;
  });

  const allValid = useMemo(() => {
    if (!props.fields) return true;
    return props.fields.every((f) => {
      if (!f.required) return true;
      const val = values[f.id];
      return val !== undefined && val !== "";
    });
  }, [props.fields, values]);

  const handleChange = (id, val) => {
    setValues((v) => ({ ...v, [id]: val }));
  };

  const renderField = (field) => {
    const value = values[field.id];
    if (field.type === "select") {
      return (
        <Select
          value={value}
          onValueChange={(val) => handleChange(field.id, val)}
        >
          <SelectTrigger id={field.id}>
            <SelectValue placeholder={`Select ${field.label}`} />
          </SelectTrigger>
          <SelectContent>
            {(field.options || []).map((opt) => (
              <SelectItem key={opt} value={opt}>
                {opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
    }
    return (
      <Input
        id={field.id}
        type={field.type || "text"}
        value={value}
        placeholder={field.placeholder || ""}
        onChange={(e) => handleChange(field.id, e.target.value)}
      />
    );
  };

  return (
    <Card className="mt-4 w-full max-w-2xl">
      <CardHeader>
        <CardTitle>🚀 Pipeline Configuration</CardTitle>
        <CardDescription>
          Fill in the details below and click Submit to trigger your deployment
          pipeline.
        </CardDescription>
      </CardHeader>

      <CardContent className="grid grid-cols-2 gap-4">
        {(props.fields || []).map((field) => (
          <div
            key={field.id}
            className={`flex flex-col gap-2 ${
              field.id === "bitbucket_url" ? "col-span-2" : ""
            }`}
          >
            <Label htmlFor={field.id}>
              {field.label}
              {field.required && (
                <span className="text-red-500 ml-1">*</span>
              )}
            </Label>
            {renderField(field)}
          </div>
        ))}
      </CardContent>

      <CardFooter className="flex justify-end gap-2">
        <Button
          id="pipeline-cancel"
          variant="outline"
          onClick={() => cancelElement()}
        >
          Cancel
        </Button>
        <Button
          id="pipeline-submit"
          disabled={!allValid}
          onClick={() => submitElement(values)}
        >
          Submit Configuration
        </Button>
      </CardFooter>
    </Card>
  );
}
