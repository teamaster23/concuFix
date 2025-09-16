
    public interface MathOperations {
        default double square(double num) {
            return num * num;
        }

        static int factorial(int n) {
            if (n == 0) return 1;
            return n * factorial(n - 1);
        }
    }
    