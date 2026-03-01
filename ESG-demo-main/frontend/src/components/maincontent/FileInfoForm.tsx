import React from "react";
import { Form, Select, Space } from "antd";
import type { UploadFile } from "antd/es/upload/interface";
import type { FormInstance } from "antd/es/form";
import { industries } from "@/data/industries";
import { useT } from "@/i18n/useT";

interface FileInfoFormProps {
  form: FormInstance<{
    category: string;
    description: string;
    tags: string[];
    industry: string;
    semiIndustry: string;
    framework: string;
  }>;
  selectedUploadFile: UploadFile | null;
  selectedIndustry: string;
  onIndustryChange: (value: string) => void;
}

const FileInfoForm: React.FC<FileInfoFormProps> = ({
  form,
  selectedUploadFile,
  selectedIndustry,
  onIndustryChange,
}) => {
  const { t } = useT();
  const framework = Form.useWatch("framework", form);
  const isSASBSelected = framework === "SASB";

  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={{
        category: "document",
        tags: [],
        industry: "",
        semiIndustry: "",
        framework: "",
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
            { label: "TCFD", value: "TCFD" },
          ]}
          onChange={(value) => {
            if (value !== "SASB") {
              form.setFieldsValue({
                industry: undefined,
                semiIndustry: undefined,
              });
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
            onIndustryChange(value);
            form.setFieldsValue({ semiIndustry: undefined });
          }}
        >
          {Object.keys(industries).map((industry) => (
            <Select.Option key={industry} value={industry}>
              {industry}
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
          {selectedIndustry &&
            industries[selectedIndustry].map((semiIndustry) => (
              <Select.Option key={semiIndustry} value={semiIndustry}>
                {semiIndustry}
              </Select.Option>
            ))}
        </Select>
      </Form.Item>
    </Form>
  );
};

export default FileInfoForm;
