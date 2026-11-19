# Ant Design of React

`antd` 是基于 Ant Design 设计体系的 React UI 组件库，适合企业级中后台产品与前台桌面网站。

## 特性

- 提炼自企业级中后台产品的交互语言和视觉风格。
- 开箱即用的高质量 React 组件。
- 使用 TypeScript 开发，提供完整的类型定义文件。
- 全链路开发和设计工具体系。
- 数十个国际化语言支持。
- 深入每个细节的主题定制能力。

## 兼容环境

- 现代浏览器
- 支持服务端渲染。
- Electron

`antd 2.0` 之后不再支持 IE8。`antd 4.0` 之后不再支持 React 15 和 IE9/10。`antd 5.0` 之后不再支持 IE。`antd 6.0` 之后不再支持 React 16/17。

## 安装

我们推荐使用 npm 或 yarn 或 pnpm 或 bun 的方式进行开发，不仅可在开发环境轻松调试，也可放心地在生产环境打包部署使用，享受整个生态圈和工具链带来的诸多好处。

在浏览器中使用 `script` 和 `link` 标签直接引入文件，并使用全局变量 `antd`。我们在 npm 发布包内的 dist 目录下提供了 `antd.js`、`antd.min.js` 和 `reset.css`。强烈不推荐使用已构建文件，这样无法按需加载，而且难以获得底层依赖模块的 bug 快速修复支持。注意：`antd.js` 和 `antd.min.js` 依赖 `react`、`react-dom`、`dayjs`，请确保提前引入这些文件。

## 在 Vite 中使用

Vite 是业界最优秀的 React 应用开发工具之一，本文会尝试在 Vite 创建的工程中使用 `antd` 组件，并自定义 Vite 的配置以满足各类工程化需求。

工具会自动初始化一个脚手架并安装 React 项目的各种必要依赖，如果在过程中出现网络问题，请尝试配置代理，或使用其他 npm registry。此时使用浏览器访问 http://localhost:5173/ ，看到 `Vite + React` 的界面就算成功了。

现在从 yarn 或 npm 或 pnpm 或 bun 安装并引入 antd。修改应用入口文件，引入 antd 的按钮组件后，你应该能看到页面上已经有了 `antd` 的蓝色按钮组件，接下来就可以继续选用其他组件开发应用了。
