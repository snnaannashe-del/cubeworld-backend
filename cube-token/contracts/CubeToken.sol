// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * CubeWorld — CUBE Token
 * ERC-20 на Polygon (MATIC). Дешёвые транзакции ~$0.001
 *
 * Логика:
 *  - Владелец (owner) деплоит контракт и получает начальный запас
 *  - Minter (бэкенд CubeWorld) может минтить CUBE как награды пользователям
 *  - Максимальный Supply: 1 миллиард CUBE
 *  - Пользователи могут сжигать свои токены (burn)
 *  - Трансферы стандартные ERC-20
 */

interface IERC20 {
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
    function allowance(address owner, address spender) external view returns (uint256);
    function approve(address spender, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

contract CubeToken is IERC20 {
    string public constant name     = "CubeWorld";
    string public constant symbol   = "CUBE";
    uint8  public constant decimals = 18;
    uint256 public constant MAX_SUPPLY = 1_000_000_000 * 10**18;
    uint256 private _totalSupply;
    mapping(address => uint256) private _balances;
    mapping(address => mapping(address => uint256)) private _allowances;
    address public owner;
    address public minter;
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event MinterUpdated(address indexed previousMinter, address indexed newMinter);
    event Mint(address indexed to, uint256 amount, string reason);
    event Burn(address indexed from, uint256 amount);
    modifier onlyOwner() { require(msg.sender == owner, "CUBE: not owner"); _; }
    modifier onlyMinter() { require(msg.sender == minter || msg.sender == owner, "CUBE: not minter"); _; }
    constructor(address _minter) {
        owner = msg.sender; minter = _minter;
        uint256 initial = 50_000_000 * 10**18;
        _mint(msg.sender, initial);
        emit MinterUpdated(address(0), _minter);
    }
    function totalSupply() external view override returns (uint256) { return _totalSupply; }
    function balanceOf(address account) external view override returns (uint256) { return _balances[account]; }
    function transfer(address to, uint256 value) external override returns (bool) { _transfer(msg.sender, to, value); return true; }
    function allowance(address _owner, address spender) external view override returns (uint256) { return _allowances[_owner][spender]; }
    function approve(address spender, uint256 value) external override returns (bool) { _approve(msg.sender, spender, value); return true; }
    function transferFrom(address from, address to, uint256 value) external override returns (bool) {
        uint256 allowed = _allowances[from][msg.sender];
        require(allowed >= value, "CUBE: insufficient allowance");
        _allowances[from][msg.sender] = allowed - value;
        _transfer(from, to, value); return true;
    }
    function mint(address to, uint256 amount, string calldata reason) external onlyMinter {
        require(_totalSupply + amount <= MAX_SUPPLY, "CUBE: max supply exceeded");
        _mint(to, amount); emit Mint(to, amount, reason);
    }
    function mintBatch(address[] calldata recipients, uint256[] calldata amounts, string calldata reason) external onlyMinter {
        require(recipients.length == amounts.length, "CUBE: length mismatch");
        uint256 total; for (uint256 i = 0; i < amounts.length; i++) total += amounts[i];
        require(_totalSupply + total <= MAX_SUPPLY, "CUBE: max supply exceeded");
        for (uint256 i = 0; i < recipients.length; i++) { _mint(recipients[i], amounts[i]); emit Mint(recipients[i], amounts[i], reason); }
    }
    function burn(uint256 amount) external {
        require(_balances[msg.sender] >= amount, "CUBE: insufficient balance");
        _balances[msg.sender] -= amount; _totalSupply -= amount;
        emit Burn(msg.sender, amount); emit Transfer(msg.sender, address(0), amount);
    }
    function setMinter(address newMinter) external onlyOwner { emit MinterUpdated(minter, newMinter); minter = newMinter; }
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "CUBE: zero address");
        emit OwnershipTransferred(owner, newOwner); owner = newOwner;
    }
    function _transfer(address from, address to, uint256 value) internal {
        require(to != address(0), "CUBE: transfer to zero address");
        require(_balances[from] >= value, "CUBE: insufficient balance");
        _balances[from] -= value; _balances[to] += value;
        emit Transfer(from, to, value);
    }
    function _mint(address to, uint256 value) internal {
        _totalSupply += value; _balances[to] += value;
        emit Transfer(address(0), to, value);
    }
    function _approve(address _owner, address spender, uint256 value) internal {
        _allowances[_owner][spender] = value;
        emit Approval(_owner, spender, value);
    }
}
