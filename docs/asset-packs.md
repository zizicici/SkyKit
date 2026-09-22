# 远期星历资源发布

App 内置 1850–2149 年星历。iOS 15–18 只使用内置数据；iOS 26+ 使用
Apple-Hosted Background Assets，将 2150–2649 年作为**一个完整包**按需下载。
不使用 ODR，也不在客户端直连 JPL。算法、数据精度和现有内置范围不变。

## 工程配置

- App：`com.zizicici.moontake`，最低 iOS 15。
- ExtensionKit 下载扩展：`com.zizicici.moontake.LunarAssetDownloader`，最低 iOS 26。
- 两个 target 共用 App Group：`group.com.zizicici.moontake`。
- 主 App 的 Info.plist 设置 `BAAppGroupID`、`BAHasManagedAssetPacks` 和
  `BAUsesAppleHosting`。扩展使用系统 `StoreDownloaderExtension` 默认实现。
- 资源 ID：`skykit-de440-2150-2649-v1`；策略为 `onDemand`，不会安装时自动下载。
- 包内路径：`SkyKit/de440/2150-2649.bin`。

首次查看远期照片时，现有加载状态等待系统下载整个包；成功后可离线使用。
失败显示月相不可用，后续请求可重试。日常找月亮不需要这个包。
底层加载器校验固定 SHA-256；更换系数内容时需要同时更新目录及资源 ID。

## 生成和验证

先按原型 README 准备 Python 环境（NumPy/JPLEphem）和原生验证库，再运行：

```sh
SKYKIT_CACHE=/path/to/cache /path/to/venv/bin/python scripts/build-asset-packs.py --fetch
SKYKIT_CACHE=/path/to/cache /path/to/venv/bin/python scripts/validate-asset-packs.py
swift test
```

第一次从 JPL 下载必需的原始记录；之后有缓存时可以省略 `--fetch` 完全离线构建。
工具用 `xcrun ba-package` 生成：

`build/asset-packs/skykit-de440-2150-2649-v1.aar`

系数文件为 35,073,756 字节，归档约 33 MiB。生成文件夹被 Git 忽略；
运行时目录、源记录校验值及验证报告位于 仓库根目录，应与代码一起保留。
验证逐字节比较全部原始系数，并抽查原生读取器每个插值区间的边界。
`--fetch` 仅授权构建时获取数据；脚本不会上传或发布。

## 上线前

1. 在 Apple Developer 为 App 与扩展注册上述 App Group，并更新两个 target 的签名配置。
2. 使用 App Store Connect 的 Apple-Hosted Asset Packs 流程，通过 Transporter 或
   苹果支持的上传工具上传这**一个** `.aar`，等待处理完成。
3. 在 TestFlight 验证首次下载、断网失败后重试，以及退出重开后的离线读取。
   资源下载成功前不能把远期功能视为已上线。

本地单元测试注入托管资源源，验证下载策略和数据加载；不能代替 Apple 服务器端到端测试。
开发中也可使用苹果 `ba-serve` 配置 HTTPS 测试服务器及测试设备的 Developer URL Override。
不要将本地 mock URL 或开发证书带入发布配置。

## 精度边界

远期包扩展的是位置系数覆盖范围，不代表未来闰秒或地球自转已经可知。
现有未来 UTC 和 EOP 质量标记继续生效；2650 年及以后明确不可用。

- [Apple-hosted asset packs](https://developer.apple.com/documentation/backgroundassets/downloading-apple-hosted-asset-packs)
- [Creating managed asset packs](https://developer.apple.com/documentation/backgroundassets/creating-managed-asset-packs)
- [Local testing](https://developer.apple.com/documentation/backgroundassets/testing-asset-packs-locally)
- [Uploading asset packs](https://developer.apple.com/help/app-store-connect/manage-asset-packs/upload-apple-hosted-asset-packs)
