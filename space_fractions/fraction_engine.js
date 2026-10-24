/**
 * Space Fractions — Mathematical Fraction Engine & Question Generator.
 * 
 * Provides:
 * 1. Exact fraction arithmetic (simplify, add, subtract, compare, equals).
 * 2. Deterministic seeded pseudo-random number generator (Mulberry32).
 * 3. Dynamic SVG generators for visual fraction pies and partitioned fraction bars.
 * 4. Progressive question bank generator covering Grade 6 math curriculum.
 * 
 * Zero dependencies, zero external network requests.
 */

class Fraction {
  constructor(numerator, denominator) {
    if (!Number.isInteger(numerator) || !Number.isInteger(denominator)) {
      throw new Error("Fraction components must be integers.");
    }
    if (denominator === 0) {
      throw new Error("Denominator cannot be zero.");
    }
    // Normalize negative signs to numerator
    if (denominator < 0) {
      numerator = -numerator;
      denominator = -denominator;
    }
    this.n = numerator;
    this.d = denominator;
  }

  static gcd(a, b) {
    a = Math.abs(a);
    b = Math.abs(b);
    while (b !== 0) {
      const temp = b;
      b = a % b;
      a = temp;
    }
    return a;
  }

  static lcm(a, b) {
    if (a === 0 || b === 0) return 0;
    return Math.abs(a * b) / Fraction.gcd(a, b);
  }

  simplify() {
    const common = Fraction.gcd(this.n, this.d);
    return new Fraction(this.n / common, this.d / common);
  }

  add(other) {
    const commonD = Fraction.lcm(this.d, other.d);
    const scaleA = commonD / this.d;
    const scaleB = commonD / other.d;
    const newN = (this.n * scaleA) + (other.n * scaleB);
    return new Fraction(newN, commonD).simplify();
  }

  subtract(other) {
    const commonD = Fraction.lcm(this.d, other.d);
    const scaleA = commonD / this.d;
    const scaleB = commonD / other.d;
    const newN = (this.n * scaleA) - (other.n * scaleB);
    return new Fraction(newN, commonD).simplify();
  }

  equals(other) {
    const simpA = this.simplify();
    const simpB = other.simplify();
    return simpA.n === simpB.n && simpA.d === simpB.d;
  }

  compare(other) {
    const diff = (this.n * other.d) - (other.n * this.d);
    if (diff > 0) return 1;
    if (diff < 0) return -1;
    return 0;
  }

  toString() {
    if (this.d === 1) return `${this.n}`;
    return `${this.n}/${this.d}`;
  }

  toLatex() {
    return `${this.n}/${this.d}`;
  }
}

/**
 * Seedable PRNG (Mulberry32) for reproducible question sets.
 */
class SeededRandom {
  constructor(seed = 42) {
    this.seed = seed;
  }

  next() {
    let t = (this.seed += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }

  nextInt(min, max) {
    return Math.floor(this.next() * (max - min + 1)) + min;
  }

  shuffle(array) {
    const arr = [...array];
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(this.next() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  }
}

/**
 * SVG Visual Representation Generator.
 */
class VisualFractionRenderer {
  /**
   * Generates an SVG Pie chart representation of a fraction.
   */
  static renderPie(n, d, radius = 50) {
    const cx = 60;
    const cy = 60;
    const totalSlices = d;
    const filledSlices = n;
    const angleStep = (2 * Math.PI) / totalSlices;

    let paths = [];

    // Background circle
    paths.push(`<circle cx="${cx}" cy="${cy}" r="${radius}" fill="#1e293b" stroke="#475569" stroke-width="2"/>`);

    for (let i = 0; i < totalSlices; i++) {
      const startAngle = i * angleStep - Math.PI / 2;
      const endAngle = (i + 1) * angleStep - Math.PI / 2;

      const x1 = cx + radius * Math.cos(startAngle);
      const y1 = cy + radius * Math.sin(startAngle);
      const x2 = cx + radius * Math.cos(endAngle);
      const y2 = cy + radius * Math.sin(endAngle);

      const largeArc = angleStep > Math.PI ? 1 : 0;
      const isFilled = i < filledSlices;
      const fillColor = isFilled ? "#3b82f6" : "transparent";

      const dPath = `M ${cx} ${cy} L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z`;
      paths.push(`<path d="${dPath}" fill="${fillColor}" stroke="#334155" stroke-width="1.5"/>`);
    }

    return `<svg width="120" height="120" viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg">
      ${paths.join("\n")}
    </svg>`;
  }

  /**
   * Generates an SVG Fraction Bar representation.
   */
  static renderBar(n, d, width = 240, height = 36) {
    const segmentWidth = width / d;
    let rects = [];

    rects.push(`<rect x="0" y="0" width="${width}" height="${height}" rx="4" fill="#1e293b" stroke="#475569" stroke-width="2"/>`);

    for (let i = 0; i < d; i++) {
      const x = i * segmentWidth;
      const isFilled = i < n;
      const fill = isFilled ? "#06b6d4" : "transparent";
      rects.push(`<rect x="${x}" y="0" width="${segmentWidth}" height="${height}" fill="${fill}" stroke="#334155" stroke-width="1.5"/>`);
    }

    return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
      ${rects.join("\n")}
    </svg>`;
  }
}

/**
 * Question Generator aligned with Grade 6 curriculum.
 */
class QuestionGenerator {
  constructor(seed = 42) {
    this.rng = new SeededRandom(seed);
  }

  generateQuestion(level = 1, index = 1) {
    switch (level) {
      case 1:
        return this._generateLevel1(index);
      case 2:
        return this._generateLevel2(index);
      case 3:
      default:
        return this._generateLevel3(index);
    }
  }

  generate(level = 1, index = 1) {
    return this.generateQuestion(level, index);
  }

  _generateLevel1(index) {
    // Level 1: Simplification and Equivalent Fractions
    const isSimplify = index % 2 === 0;

    if (isSimplify) {
      const baseD = this.rng.nextInt(2, 6);
      const baseN = this.rng.nextInt(1, baseD - 1);
      const base = new Fraction(baseN, baseD).simplify();
      const multiplier = this.rng.nextInt(2, 4);

      const unsimpN = base.n * multiplier;
      const unsimpD = base.d * multiplier;

      const prompt = `Simplify the spacecraft fuel ratio: ${unsimpN}/${unsimpD}`;
      const correctAns = `${base.n}/${base.d}`;
      const visualSvg = VisualFractionRenderer.renderPie(unsimpN, unsimpD);

      const distractorCands = [
        `${base.n + 1}/${base.d}`,
        `${base.n}/${base.d + 1}`,
        `${base.n * 2}/${base.d * 2 + 1}`,
        `${Math.max(1, base.n - 1)}/${base.d + 2}`,
      ];

      const uniqueOptions = [correctAns];
      for (const c of distractorCands) {
        if (!uniqueOptions.includes(c)) uniqueOptions.push(c);
        if (uniqueOptions.length === 4) break;
      }
      while (uniqueOptions.length < 4) {
        const fallback = `${uniqueOptions.length + 1}/${base.d + uniqueOptions.length}`;
        if (!uniqueOptions.includes(fallback)) uniqueOptions.push(fallback);
      }

      const options = this.rng.shuffle(uniqueOptions);
      const correctIndex = options.indexOf(correctAns);

      return {
        id: `L1-Q${index}`,
        level: 1,
        topic: "Fraction Simplification",
        prompt: prompt,
        visualSvg: visualSvg,
        options: options,
        correctIndex: correctIndex,
        explanation: `To simplify ${unsimpN}/${unsimpD}, divide both numerator and denominator by their greatest common divisor (${multiplier}): ${unsimpN} ÷ ${multiplier} = ${base.n}, and ${unsimpD} ÷ ${multiplier} = ${base.d}. The simplest form is ${correctAns}.`,
      };
    } else {
      // Equivalent fractions
      const baseD = this.rng.nextInt(2, 5);
      const baseN = this.rng.nextInt(1, baseD - 1);
      const mult = this.rng.nextInt(2, 5);
      const targetN = baseN * mult;
      const targetD = baseD * mult;

      const prompt = `Identify the fraction equivalent to the engine charge: ${baseN}/${baseD}`;
      const correctAns = `${targetN}/${targetD}`;
      const visualSvg = VisualFractionRenderer.renderBar(baseN, baseD);

      const distractorCands = [
        `${targetN + 1}/${targetD}`,
        `${targetN}/${targetD + 2}`,
        `${Math.max(1, targetN - 1)}/${targetD + 1}`,
        `${targetN + 2}/${targetD}`,
      ];

      const uniqueOptions = [correctAns];
      for (const c of distractorCands) {
        if (!uniqueOptions.includes(c)) uniqueOptions.push(c);
        if (uniqueOptions.length === 4) break;
      }
      while (uniqueOptions.length < 4) {
        const fallback = `${targetN + uniqueOptions.length}/${targetD + uniqueOptions.length}`;
        if (!uniqueOptions.includes(fallback)) uniqueOptions.push(fallback);
      }

      const options = this.rng.shuffle(uniqueOptions);
      const correctIndex = options.indexOf(correctAns);

      return {
        id: `L1-Q${index}`,
        level: 1,
        topic: "Equivalent Fractions",
        prompt: prompt,
        visualSvg: visualSvg,
        options: options,
        correctIndex: correctIndex,
        explanation: `Multiplying both the top and bottom of ${baseN}/${baseD} by ${mult} gives (${baseN} × ${mult}) / (${baseD} × ${mult}) = ${targetN}/${targetD}.`,
      };
    }
  }

  _generateLevel2(index) {
    // Level 2: Comparing fractions and addition with like denominators
    const isAddition = index % 2 === 0;

    if (isAddition) {
      const denom = this.rng.nextInt(4, 10);
      const n1 = this.rng.nextInt(1, Math.floor(denom / 2));
      const n2 = this.rng.nextInt(1, denom - n1 - 1);
      const f1 = new Fraction(n1, denom);
      const f2 = new Fraction(n2, denom);
      const sum = f1.add(f2);

      const prompt = `Calculate the total energy output: ${f1.toString()} + ${f2.toString()}`;
      const correctAns = sum.toString();
      const visualSvg = VisualFractionRenderer.renderBar(n1 + n2, denom);

      const distractorCands = [
        `${n1 + n2}/${denom * 2}`,
        `${n1 + n2 + 1}/${denom}`,
        `${Math.max(1, n1 + n2 - 1)}/${denom}`,
        `${n1 + n2 + 2}/${denom}`,
        `${n1 + n2}/${denom + 1}`,
      ];

      const uniqueOptions = [correctAns];
      for (const c of distractorCands) {
        if (!uniqueOptions.includes(c)) uniqueOptions.push(c);
        if (uniqueOptions.length === 4) break;
      }
      while (uniqueOptions.length < 4) {
        const fallback = `${uniqueOptions.length + 1}/${denom}`;
        if (!uniqueOptions.includes(fallback)) uniqueOptions.push(fallback);
      }

      const options = this.rng.shuffle(uniqueOptions);
      const correctIndex = options.indexOf(correctAns);

      return {
        id: `L2-Q${index}`,
        level: 2,
        topic: "Addition (Common Denominator)",
        prompt: prompt,
        visualSvg: visualSvg,
        options: options,
        correctIndex: correctIndex,
        explanation: `When denominators are the same, add the numerators directly: (${n1} + ${n2}) / ${denom} = ${n1 + n2}/${denom}. Simplified form is ${correctAns}.`,
      };
    } else {
      // Comparison
      const d1 = this.rng.nextInt(3, 8);
      const n1 = this.rng.nextInt(1, d1 - 1);
      const d2 = this.rng.nextInt(3, 8);
      let n2 = this.rng.nextInt(1, d2 - 1);
      if (n1 * d2 === n2 * d1) n2 = Math.min(d2 - 1, n2 + 1); // Ensure distinct

      const f1 = new Fraction(n1, d1);
      const f2 = new Fraction(n2, d2);

      const prompt = `Compare planetary mass fractions: Which statement is true?`;
      const isGreater = f1.compare(f2) > 0;
      const correctAns = isGreater ? `${f1.toString()} > ${f2.toString()}` : `${f1.toString()} < ${f2.toString()}`;
      const opposite = isGreater ? `${f1.toString()} < ${f2.toString()}` : `${f1.toString()} > ${f2.toString()}`;

      const options = this.rng.shuffle([
        correctAns,
        opposite,
        `${f1.toString()} = ${f2.toString()}`,
        `Cannot be determined without a telescope`,
      ]);
      const correctIndex = options.indexOf(correctAns);

      const visualSvg = VisualFractionRenderer.renderBar(n1, d1, 200, 30);

      const cross1 = n1 * d2;
      const cross2 = n2 * d1;

      return {
        id: `L2-Q${index}`,
        level: 2,
        topic: "Fraction Comparison",
        prompt: prompt,
        visualSvg: visualSvg,
        options: options,
        correctIndex: correctIndex,
        explanation: `Cross-multiply to compare: ${n1} × ${d2} = ${cross1}, and ${n2} × ${d1} = ${cross2}. Since ${cross1} ${isGreater ? '>' : '<'} ${cross2}, ${correctAns}.`,
      };
    }
  }

  _generateLevel3(index) {
    // Level 3: Addition and Subtraction with different denominators
    const isSub = index % 2 === 0;
    const d1 = this.rng.nextInt(2, 5);
    const d2 = this.rng.nextInt(3, 6);
    // ensure different denominators
    const actualD2 = d1 === d2 ? d2 + 1 : d2;

    const n1 = this.rng.nextInt(1, d1 - 1);
    const n2 = this.rng.nextInt(1, actualD2 - 1);

    const f1 = new Fraction(n1, d1);
    const f2 = new Fraction(n2, actualD2);

    let resultFraction;
    let opStr;
    let term1, term2;

    if (isSub) {
      if (f1.compare(f2) < 0) {
        term1 = f2;
        term2 = f1;
      } else {
        term1 = f1;
        term2 = f2;
      }
      resultFraction = term1.subtract(term2);
      opStr = "-";
    } else {
      term1 = f1;
      term2 = f2;
      resultFraction = term1.add(term2);
      opStr = "+";
    }

    const prompt = `Solve thruster vector equation: ${term1.toString()} ${opStr} ${term2.toString()}`;
    const correctAns = resultFraction.toString();

    const lcd = Fraction.lcm(term1.d, term2.d);
    const scale1 = lcd / term1.d;
    const scale2 = lcd / term2.d;
    const adjN1 = term1.n * scale1;
    const adjN2 = term2.n * scale2;

    const visualSvg = VisualFractionRenderer.renderPie(resultFraction.n, Math.max(resultFraction.d, resultFraction.n));

    const distractorCands = [
      `${term1.n + term2.n}/${term1.d + term2.d}`, // classic misconception!
      `${Math.abs(adjN1 - adjN2) + 1}/${lcd}`,
      `${adjN1 + adjN2 + 2}/${lcd}`,
      `${resultFraction.n + 1}/${resultFraction.d}`,
      `${Math.max(1, resultFraction.n - 1)}/${resultFraction.d}`,
      `${resultFraction.n}/${resultFraction.d + 1}`,
      `${resultFraction.n + 2}/${lcd}`,
      `${Math.max(1, resultFraction.n - 2)}/${lcd}`,
    ];

    const uniqueOptions = [correctAns];
    for (const c of distractorCands) {
      if (!uniqueOptions.includes(c)) uniqueOptions.push(c);
      if (uniqueOptions.length === 4) break;
    }
    while (uniqueOptions.length < 4) {
      const fallback = `${uniqueOptions.length + 1}/${lcd}`;
      if (!uniqueOptions.includes(fallback)) uniqueOptions.push(fallback);
    }

    const options = this.rng.shuffle(uniqueOptions);
    const correctIndex = options.indexOf(correctAns);

    return {
      id: `L3-Q${index}`,
      level: 3,
      topic: isSub ? "Subtraction (Unlike Denominators)" : "Addition (Unlike Denominators)",
      prompt: prompt,
      visualSvg: visualSvg,
      options: options,
      correctIndex: correctIndex,
      explanation: `Find the least common denominator of ${term1.d} and ${term2.d}, which is ${lcd}. Convert: ${term1.toString()} = ${adjN1}/${lcd}, and ${term2.toString()} = ${adjN2}/${lcd}. Then compute (${adjN1} ${opStr} ${adjN2}) / ${lcd} = ${resultFraction.toString()}.`,
    };
  }
}

// Export for testing or module environments
if (typeof module !== "undefined" && module.exports) {
  module.exports = { Fraction, SeededRandom, QuestionGenerator, VisualFractionRenderer };
}
