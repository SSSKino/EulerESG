"use client";

import React, { useEffect, useMemo, useState } from "react";
import { Form, Modal, Select } from "antd";
import { useT } from "@/i18n/useT";

import { industries, SASB_OTHER_INDUSTRY_KEY } from "@/data/industries";
import { CDP_TOPIC_OPTIONS } from "@/data/cdpTopics";
import { TCFD_TOPIC_OPTIONS } from "@/data/tcfdTopics";

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
  const isCDPSelected = framework === "CDP";
  const isTCFDSelected = framework === "TCFD";
  const isTopicOnlyFramework = isCDPSelected || isTCFDSelected;

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
    } else if (out.framework === "CDP") {
      out.industry = "CDP";
      out.semiIndustry = safeTrim(values.semiIndustry) || undefined;
    } else if (out.framework === "TCFD") {
      out.industry = "TCFD";
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
              { label: "CDP", value: "CDP" },
              { label: "TCFD", value: "TCFD" },
            ]}
            onChange={(value) => {
              if (value === "SASB") {
                form.setFieldsValue({ semiIndustry: undefined } as any);
                return;
              }
              setSelectedIndustry("");
              form.setFieldsValue({ industry: undefined, semiIndustry: undefined } as any);
            }}
          />
        </Form.Item>

        <Form.Item
          name="industry"
          label={t("upload.industry")}
          rules={[{ required: isSASBSelected, message: t("upload.pleaseSelectIndustry") }]}
          hidden={isTopicOnlyFramework}
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
                {ind === SASB_OTHER_INDUSTRY_KEY ? t("upload.sasbIndustryOther") : ind}
              </Select.Option>
            ))}
          </Select>
        </Form.Item>

        <Form.Item
          name="semiIndustry"
          label={
            isCDPSelected ? t("upload.cdpTopic") : isTCFDSelected ? t("upload.tcfdTopic") : t("upload.subIndustry")
          }
          rules={[
            {
              required: isSASBSelected || isTopicOnlyFramework,
              message: isCDPSelected
                ? t("upload.pleaseSelectCdpTopic")
                : isTCFDSelected
                  ? t("upload.pleaseSelectTcfdTopic")
                  : t("upload.pleaseSelectSubIndustry"),
            },
          ]}
        >
          <Select
            placeholder={
              isCDPSelected
                ? t("upload.selectCdpTopic")
                : isTCFDSelected
                  ? t("upload.selectTcfdTopic")
                  : t("upload.selectSubIndustry")
            }
            disabled={isTopicOnlyFramework ? false : !isSASBSelected || !selectedIndustry}
            allowClear={isTopicOnlyFramework}
          >
            {isCDPSelected
              ? CDP_TOPIC_OPTIONS.map((o) => (
                  <Select.Option key={o.slug} value={o.slug}>
                    {o.label}
                  </Select.Option>
                ))
              : isTCFDSelected
                ? TCFD_TOPIC_OPTIONS.map((o) => (
                    <Select.Option key={o.slug} value={o.slug}>
                      {o.label}
                    </Select.Option>
                  ))
                : semiIndustryOptions.map((semi) => (
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
