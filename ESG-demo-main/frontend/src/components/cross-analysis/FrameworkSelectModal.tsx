"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Form, Modal, Select } from "antd";
import { useT } from "@/i18n/useT";

import { industries } from "@/data/industries";

export type FrameworkSelectionValues = {
  framework: string;
  industry?: string;
  semiIndustry?: string;
};

type Props = {
  open: boolean;
  initialValues?: Partial<FrameworkSelectionValues>;
  onCancel: () => void;
  onConfirm: (values: FrameworkSelectionValues) => void;
  title?: string;
};

function safeTrim(v: any): string {
  if (v === null || v === undefined) return "";
  return String(v).trim();
}

export default function FrameworkSelectModal({
  open,
  initialValues,
  onCancel,
  onConfirm,
  title = undefined,
}: Props) {
  const { t } = useT();
  const [form] = Form.useForm<FrameworkSelectionValues>();
  const [selectedIndustry, setSelectedIndustry] = useState<string>(safeTrim(initialValues?.industry));

  // Keep form values in sync with initial values when the modal opens.
  useEffect(() => {
    if (!open) return;
    const initFramework = safeTrim(initialValues?.framework);
    const initIndustry = safeTrim(initialValues?.industry);
    const initSemi = safeTrim(initialValues?.semiIndustry);
    setSelectedIndustry(initIndustry);
    form.setFieldsValue({
      framework: initFramework || undefined,
      industry: initIndustry || undefined,
      semiIndustry: initSemi || undefined,
    } as any);
  }, [open, initialValues?.framework, initialValues?.industry, initialValues?.semiIndustry, form]);

  const framework = Form.useWatch("framework", form);
  const isSASBSelected = framework === "SASB";

  const industryOptions = useMemo(() => Object.keys(industries || {}), []);

  const semiIndustryOptions = useMemo(() => {
    const key = safeTrim(selectedIndustry);
    if (!key) return [];
    const list = (industries as any)[key];
    return Array.isArray(list) ? list : [];
  }, [selectedIndustry]);

  const handleOk = async () => {
    const values = await form.validateFields();

    const out: FrameworkSelectionValues = {
      framework: safeTrim(values.framework),
    };
    if (out.framework === "SASB") {
      out.industry = safeTrim(values.industry) || undefined;
      out.semiIndustry = safeTrim(values.semiIndustry) || undefined;
    }
    onConfirm(out);
  };

  return (
    <Modal
      open={open}
      title={title ?? t("cross.selectFramework")}
      okText={t("cross.confirm")}
      cancelText={t("common.cancel")}
      onCancel={onCancel}
      onOk={handleOk}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          framework: "",
          industry: "",
          semiIndustry: "",
        }}
      >
        <Form.Item
          name="framework"
          label={t("upload.framework")}
          rules={[{ required: true, message: t("upload.pleaseSelectFramework") }]}
        >
          <Select
            placeholder={t("upload.selectFramework")}
            options={[
              { label: "SASB", value: "SASB" },
              { label: "GRI", value: "GRI" },
              { label: "TCFD", value: "TCFD" },
            ]}
            onChange={(value) => {
              if (value !== "SASB") {
                setSelectedIndustry("");
                form.setFieldsValue({ industry: undefined, semiIndustry: undefined } as any);
              }
            }}
          />
        </Form.Item>

        <Form.Item
          name="industry"
          label={t("upload.industry")}
          rules={[{ required: isSASBSelected, message: t("upload.pleaseSelectIndustry") }]}
        >
          <Select
            placeholder={t("upload.selectIndustry")}
            disabled={!isSASBSelected}
            onChange={(value) => {
              const v = safeTrim(value);
              setSelectedIndustry(v);
              form.setFieldsValue({ semiIndustry: undefined } as any);
            }}
          >
            {industryOptions.map((ind) => (
              <Select.Option key={ind} value={ind}>
                {ind}
              </Select.Option>
            ))}
          </Select>
        </Form.Item>

        <Form.Item
          name="semiIndustry"
          label={t("upload.subIndustry")}
          rules={[{ required: isSASBSelected, message: t("upload.pleaseSelectSubIndustry") }]}
        >
          <Select
            placeholder={t("upload.selectSubIndustry")}
            disabled={!isSASBSelected || !selectedIndustry}
          >
            {semiIndustryOptions.map((semi) => (
              <Select.Option key={semi} value={semi}>
                {semi}
              </Select.Option>
            ))}
          </Select>
        </Form.Item>
      </Form>
    </Modal>
  );
}
