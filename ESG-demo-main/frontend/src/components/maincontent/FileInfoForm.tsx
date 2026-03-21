import React, { useEffect, useMemo, useState } from "react";
import { Form, Select, Space, Card } from "antd";
import type { UploadFile } from "antd/es/upload/interface";
import type { FormInstance } from "antd/es/form";
import { industries, SASB_OTHER_INDUSTRY_KEY } from "@/data/industries";
import { CDP_TOPIC_OPTIONS } from "@/data/cdpTopics";
import { TCFD_TOPIC_OPTIONS } from "@/data/tcfdTopics";
import { useT } from "@/i18n/useT";
import { apiService } from "@/lib/api";

export type FileInfoFormValues = {
  category: string;
  description: string;
  tags: string[];
  industry: string;
  /** SASB sub-industries, CDP/TCFD topic slugs (multi-select = one PDF, multiple compliance JSONs). */
  semiIndustry: string | string[];
  framework: string;
  griSector?: string;
  /** GRI topic slugs (multi-select supported). */
  griTopics?: string[];
};

interface FileInfoFormProps {
  form: FormInstance<FileInfoFormValues>;
  selectedUploadFile: UploadFile | null;
  selectedIndustry: string;
  onIndustryChange: (value: string) => void;
}

interface GriOption {
  slug: string;
  label: string;
}

const FileInfoForm: React.FC<FileInfoFormProps> = ({
  form,
  selectedUploadFile,
  selectedIndustry,
  onIndustryChange,
}) => {
  const { t } = useT();
  const framework = Form.useWatch("framework", form);
  const griSector = Form.useWatch("griSector", form);
  const isSASBSelected = framework === "SASB";
  const isGRISelected = framework === "GRI";
  const isCDPSelected = framework === "CDP";
  const isTCFDSelected = framework === "TCFD";

  const [griOptions, setGriOptions] = useState<{
    sectors: GriOption[];
    topicsBySector: Record<string, GriOption[]>;
  }>({ sectors: [], topicsBySector: {} });

  useEffect(() => {
    if (!isGRISelected) return;
    apiService
      .getGriOptions()
      .then(setGriOptions)
      .catch(() => setGriOptions({ sectors: [], topicsBySector: {} }));
  }, [isGRISelected]);

  const griTopicOptions = useMemo(() => {
    if (!griSector || !griOptions.topicsBySector[griSector]) return [];
    return griOptions.topicsBySector[griSector];
  }, [griSector, griOptions.topicsBySector]);

  const handleFrameworkChange = (value: string) => {
    if (value === "SASB") {
      form.setFieldsValue({
        griSector: undefined,
        griTopics: undefined,
        industry: form.getFieldValue("industry"),
        semiIndustry: undefined,
      });
    } else if (value === "GRI") {
      form.setFieldsValue({
        industry: undefined,
        semiIndustry: undefined,
        griSector: form.getFieldValue("griSector"),
        griTopics: form.getFieldValue("griTopics"),
      });
      onIndustryChange("");
    } else if (value === "CDP" || value === "TCFD") {
      form.setFieldsValue({
        industry: undefined,
        griSector: undefined,
        griTopics: undefined,
        semiIndustry: undefined,
      });
      onIndustryChange("");
    } else {
      form.setFieldsValue({
        industry: undefined,
        semiIndustry: undefined,
        griSector: undefined,
        griTopics: undefined,
      });
      onIndustryChange("");
    }
  };

  return (
    <Form<FileInfoFormValues>
      form={form}
      layout="vertical"
      initialValues={{
        category: "document",
        tags: [],
        industry: "",
        semiIndustry: [],
        framework: "",
        griSector: undefined,
        griTopics: [],
      }}
    >
      <Form.Item label={t("upload.fileInformation")}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <p>
            {t("upload.name")}: {selectedUploadFile?.name}
          </p>
          <p>
            {t("upload.size")}:{" "}
            {selectedUploadFile?.size ? (selectedUploadFile.size / 1024).toFixed(2) : 0} KB
          </p>
          <p>
            {t("upload.type")}: {selectedUploadFile?.type || t("upload.unknown")}
          </p>
        </Space>
      </Form.Item>

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
          onChange={handleFrameworkChange}
          style={{ width: "100%" }}
        />
      </Form.Item>

      {/* SASB: Industry + Sub-industry */}
      {isSASBSelected && (
        <Card
          size="small"
          style={{
            marginBottom: 16,
            borderColor: "var(--ant-colorPrimaryBorder, #91caff)",
            background: "var(--ant-colorPrimaryBg, #e6f4ff)",
          }}
          styles={{ body: { padding: "12px 16px" } }}
        >
          <Form.Item
            name="industry"
            label={t("upload.industry")}
            rules={[{ required: true, message: t("upload.pleaseSelectIndustry") }]}
          >
            <Select
              placeholder={t("upload.selectIndustry")}
              onChange={(value) => {
                onIndustryChange(value);
                form.setFieldsValue({ semiIndustry: undefined });
              }}
              style={{ width: "100%" }}
            >
              {Object.keys(industries).map((industry) => (
                <Select.Option key={industry} value={industry}>
                  {industry === SASB_OTHER_INDUSTRY_KEY ? t("upload.sasbIndustryOther") : industry}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item
            name="semiIndustry"
            label={t("upload.subIndustry")}
            rules={[
              { required: true, message: t("upload.pleaseSelectSubIndustry") },
              {
                validator: async (_, v) => {
                  const arr = Array.isArray(v) ? v : v ? [v] : [];
                  if (arr.length < 1) throw new Error(t("upload.pleaseSelectSubIndustry"));
                },
              },
            ]}
          >
            <Select
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              placeholder={t("upload.selectSubIndustry")}
              disabled={!selectedIndustry}
              style={{ width: "100%" }}
            >
              {selectedIndustry &&
                industries[selectedIndustry].map((semiIndustry) => (
                  <Select.Option key={semiIndustry} value={semiIndustry}>
                    {semiIndustry}
                  </Select.Option>
                ))}
            </Select>
          </Form.Item>
        </Card>
      )}

      {/* CDP: questionnaire topic only */}
      {isCDPSelected && (
        <Card
          size="small"
          style={{
            marginBottom: 16,
            borderColor: "var(--ant-colorWarningBorder, #ffe58f)",
            background: "var(--ant-colorWarningBg, #fffbe6)",
          }}
          styles={{ body: { padding: "12px 16px" } }}
        >
          <Form.Item
            name="semiIndustry"
            label={t("upload.cdpTopic")}
            rules={[
              { required: true, message: t("upload.pleaseSelectCdpTopic") },
              {
                validator: async (_, v) => {
                  const arr = Array.isArray(v) ? v : v ? [v] : [];
                  if (arr.length < 1) throw new Error(t("upload.pleaseSelectCdpTopic"));
                },
              },
            ]}
          >
            <Select
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              placeholder={t("upload.selectCdpTopic")}
              style={{ width: "100%" }}
              options={CDP_TOPIC_OPTIONS.map((o) => ({ label: o.label, value: o.slug }))}
            />
          </Form.Item>
        </Card>
      )}

      {/* TCFD: pillar topic only */}
      {isTCFDSelected && (
        <Card
          size="small"
          style={{
            marginBottom: 16,
            borderColor: "var(--ant-purple-4, #d3adf7)",
            background: "var(--ant-purple-1, #f9f0ff)",
          }}
          styles={{ body: { padding: "12px 16px" } }}
        >
          <Form.Item
            name="semiIndustry"
            label={t("upload.tcfdTopic")}
            rules={[
              { required: true, message: t("upload.pleaseSelectTcfdTopic") },
              {
                validator: async (_, v) => {
                  const arr = Array.isArray(v) ? v : v ? [v] : [];
                  if (arr.length < 1) throw new Error(t("upload.pleaseSelectTcfdTopic"));
                },
              },
            ]}
          >
            <Select
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              placeholder={t("upload.selectTcfdTopic")}
              style={{ width: "100%" }}
              options={TCFD_TOPIC_OPTIONS.map((o) => ({ label: o.label, value: o.slug }))}
            />
          </Form.Item>
        </Card>
      )}

      {/* GRI: Sector + Topic */}
      {isGRISelected && (
        <Card
          size="small"
          style={{
            marginBottom: 16,
            borderColor: "var(--ant-colorSuccessBorder, #b7eb8f)",
            background: "var(--ant-colorSuccessBg, #f6ffed)",
          }}
          styles={{ body: { padding: "12px 16px" } }}
        >
          <Form.Item
            name="griSector"
            label={t("upload.griSector")}
            rules={[{ required: true, message: t("upload.selectGriSector") }]}
          >
            <Select
              placeholder={t("upload.selectGriSector")}
              onChange={() => form.setFieldsValue({ griTopics: [] })}
              style={{ width: "100%" }}
              options={griOptions.sectors.map((s) => ({ label: s.label, value: s.slug }))}
            />
          </Form.Item>
          <Form.Item
            name="griTopics"
            label={t("upload.griTopics")}
            rules={[
              { required: true, message: t("upload.selectGriTopics") },
              {
                validator: async (_, v) => {
                  const arr = Array.isArray(v) ? v : [];
                  if (arr.length < 1) throw new Error(t("upload.selectGriTopics"));
                },
              },
            ]}
          >
            <Select
              mode="multiple"
              allowClear
              maxTagCount="responsive"
              placeholder={t("upload.selectGriTopics")}
              disabled={!griSector}
              style={{ width: "100%" }}
              options={griTopicOptions.map((s) => ({ label: s.label, value: s.slug }))}
            />
          </Form.Item>
        </Card>
      )}
    </Form>
  );
};

export default FileInfoForm;
