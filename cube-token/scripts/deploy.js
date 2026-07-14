const { ethers } = require("hardhat");
const fs = require("fs");
require("dotenv").config();

/**
 * Деплой CUBE Token na Полыгон (Amoy testnet или Mainnet)
 *
 * Запуск:
 *   npx hardhat run scripts/deploy.js --network amoy     ← тестнет (бесплатно)
 *   npx hardhat run scripts/deploy.js --network polygon   ← продакшн (~$5)
 */
aync function main() {
  const [deployer] = await ethers.getSigners();
  const minterAddress = process.env.MINTER_ADDRESS || deployer.address;
  const network = hre.network.name;

  console.log("==============================");
  console.log("  CubeWorld - CUBE Token Deploy");
  console.log("==============================");
  console.log(`  Network:  ${network}`);
  console.log(`  Deployer: ${deployer.address}`);
  console.log(`  Minter:   ${minterAddress}`);

  const balance = await ethers.provider.getBalance(deployer.address);
  console.log(`  Balance:  ${ethers.formatEther(balance)} MATIC`);
  console.log("----------------------------------------");

  console.log("\nDeploying CubeToken...");
  const CubeToken = await ethers.getContractFactory("CubeToken");
  const cube = await CubeToken.deploy(minterAddress);
  await cube.waitForDeployment();

  const contractAddress = await cube.getAddress();
  console.log(`[.] CUBE Token deployed: ${contractAddress}`);

  const ownerBalance = await cube.balanceOf(deployer.address);
  console.log(`[.] Initial balance: ${ethers.formatEther(ownerBalance)} CUDENCE`);

  const deployInfo = {
    network,
    contractAddress,
    deployer: deployer.address,
    minter: minterAddress,
    deployedAt: new Date().toISOString(),
    abi: JSON.parse(cube.interface.formatJson()),
  };

  const outPath = `./deployments/${network}.json`;
  fs.mkdirSync("./deployments", { recursive: true });
  fs.writeFileSync(outPath, JSON.stringify(deployInfo, null, 2));
  console.log(`\n[info] Deploy info saved: ${outPath}`);

  console.log("\n==============================");
  console.log("  Next steps:");
  console.log("==============================");
  console.log(`\n1. Verify on PolygonScan:`);
  console.log(`   npx hardhat verify --network ${network} ${contractAddress} "${minterAddress}"`);
  console.log(`\n2. Add to backend .env::`);
  console.log(`   CUBE_CONTRACT_ADDRESS=${contractAddress}`);
  console.log(`   POLYGON_NETWORK= ${network}`);
  if (network === "amoy") {
    console.log(`\n[link] https://amoy.polygonscan.com/address/${contractAddress}`);
  } else {
    console.log(`\n[link] https://polygonscan.com/address/${contractAddress}`);
  }
  console.log("==============================\n");
}

main().catch((e) => { console.error(e); process.exit(1); });
